"""Pure frozen-policy candidate constraints over the modern evidence chain."""

from datetime import date
from decimal import Decimal, Context, ROUND_HALF_EVEN, localcontext

from app.schemas.investment_model import InvestmentEvaluationBatchView
from app.schemas.risk_engine import RiskEnginePolicyV1, RiskCandidateView, RiskCandidateBatchView, RiskProvenance
from app.services.investment_model_reducer import validate_candidate_batch, _validate_context


def _require(condition):
    if not condition:
        raise ValueError("Inconsistent Risk Engine input evidence")


def _validated(value, expected):
    _require(type(value) is expected)
    try:
        expected.model_validate(value.model_dump(warnings=False), strict=True)
    except (ValueError, TypeError):
        raise ValueError("Invalid Risk Engine input contract") from None
    def check_pit(item):
        if isinstance(item, dict):
            for name, child in item.items():
                if name == "pit_ready": _require(child is False)
                check_pit(child)
        elif isinstance(item, (tuple, list)):
            for child in item: check_pit(child)
    check_pit(value.model_dump(warnings=False))


def _finite(value, minimum=None, maximum=None):
    _require(type(value) is Decimal and value.is_finite())
    _require(minimum is None or value >= minimum)
    _require(maximum is None or value <= maximum)


def validate_investment_batch(batch):
    _validated(batch, InvestmentEvaluationBatchView)
    source = batch.source_batch
    validate_candidate_batch(source)  # Existing pure validation; never recalculate Investment Score.
    _require(type(batch.as_of_date) is date and (batch.as_of_date, batch.market_source) == (source.as_of_date, source.market_source))
    _require(batch.candidate_bond_ids == source.candidate_bond_ids and tuple(e.bond_id for e in batch.evaluations) == source.candidate_bond_ids)
    _require(type(batch.candidate_count) is int and batch.candidate_count == len(batch.evaluations))
    ready = tuple(e for e in batch.evaluations if e.status == "READY")
    _require(type(batch.ready_count) is int and batch.ready_count == len(ready) and
             type(batch.unavailable_count) is int and batch.unavailable_count == len(batch.evaluations)-len(ready))
    _require(batch.normalization_bond_ids == tuple(e.bond_id for e in ready))
    _require(len(batch.ranked_bond_ids) == len(ready) and set(batch.ranked_bond_ids) == set(batch.normalization_bond_ids))
    originals = {c.bond_id: c for c in source.candidates}
    for e in batch.evaluations:
        c, f, p = e.candidate, e.candidate.features, e.provenance
        _require(c.model_dump() == originals[e.bond_id].model_dump())
        _require((e.bond_id, e.isin, e.secid, e.as_of_date, e.market_source) ==
                 (c.bond_id, c.isin, c.secid, c.as_of_date, c.market_source))
        for name in ("yield_to_maturity_pct", "spread_to_ofz_bps", "reference_ofz_yield_pct", "modified_duration_years", "dv01_currency_per_bond", "liquidity_score_v1"):
            _require(getattr(e, name) == getattr(f, name))
        _require(e.liquidity_score == f.liquidity_score_v1)
        _finite(f.relative_price_sensitivity_per_1bp, Decimal("0"))
        _require((p.batch_candidate_bond_ids, p.batch_ready_bond_ids, p.target_market_snapshot_id, p.curve_trade_date, p.m3_as_of_date) ==
                 (batch.candidate_bond_ids, batch.normalization_bond_ids, f.market_snapshot_id, f.curve_trade_date, c.as_of_date))
        _require(p.policy == batch.policy)
        contexts = e.attempted_credit_contexts
        for ctx in contexts:
            _require(ctx.bond_id == e.bond_id)
            _validate_context(ctx, originals)
        _require(len({(ctx.target_kind, ctx.rating_agency) for ctx in contexts}) == len(contexts))
        ready_contexts = tuple(ctx for ctx in contexts if ctx.distribution.status == "READY")
        _require(e.ready_credit_contexts == ready_contexts)
        if e.status == "READY":
            _require(bool(ready_contexts) and e.selected_credit_context in ready_contexts)
            _require(type(e.rank) is int and e.rank > 0 and batch.ranked_bond_ids[e.rank-1:e.rank] == (e.bond_id,))
            for name in ("investment_score_v1", "ofz_relative_value_percentile", "ofz_relative_value_score", "credit_cohort_relative_value_score", "duration_percentile", "duration_score"):
                _finite(getattr(e, name), Decimal("0"), Decimal("100"))
            _require(e.selected_credit_context.distribution.target_spread_percentile == e.credit_cohort_relative_value_score)
        else:
            _require(not ready_contexts and e.selected_credit_context is None and e.rank is None)
            _require(all(getattr(e, name) is None for name in ("investment_score_v1", "ofz_relative_value_percentile", "ofz_relative_value_score", "credit_cohort_relative_value_score", "duration_percentile", "duration_score")))
        credit = c.m3.credit
        _require((f.legal_issuer_id, f.legal_issuer_source_issuer_id, f.legal_issuer_inn) ==
                 (credit.legal_issuer_id, credit.legal_issuer_source_issuer_id, credit.legal_issuer_inn))
        verified = credit.issuer_link_status == "VERIFIED"
        _require(credit.availability.has_verified_legal_issuer is verified)
        if verified:
            _require(type(f.legal_issuer_id) is int and f.legal_issuer_id > 0)
            p = credit.provenance
            _require(type(p.bond_legal_issuer_profile_id) is int and p.bond_legal_issuer_profile_id > 0 and
                     p.mapping_state == "verified" and p.mapping_source == p.legal_issuer_identity_source == "moex_security_reference")
            _require(type(f.legal_issuer_source_issuer_id) is str and bool(f.legal_issuer_source_issuer_id.strip()) and
                     p.mapping_source_issuer_id == f.legal_issuer_source_issuer_id)
        else:
            _require(f.legal_issuer_id is None)
    scores = [e.investment_score_v1 for e in ready]
    _require(batch.score_min == (min(scores) if scores else None) and batch.score_max == (max(scores) if scores else None))


def _candidate(evaluation, policy):
    e, f = evaluation, evaluation.candidate.features
    reasons = []
    if e.status != "READY": reasons.append("INVESTMENT_MODEL_NOT_READY")
    if f.legal_issuer_id is None: reasons.append("ISSUER_IDENTITY_UNAVAILABLE")
    if f.liquidity_score_v1 < policy.min_liquidity_score: reasons.append("LIQUIDITY_SCORE_BELOW_MINIMUM")
    if f.median_daily_turnover_value == 0: reasons.append("LIQUIDITY_CAPACITY_UNAVAILABLE")
    if f.modified_duration_years > policy.max_candidate_modified_duration_years: reasons.append("CANDIDATE_DURATION_LIMIT_EXCEEDED")
    status = "INVESTMENT_MODEL_NOT_READY" if e.status != "READY" else "BLOCKED" if reasons else "ELIGIBLE"
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        capacity = f.median_daily_turnover_value * policy.max_position_to_median_daily_turnover
    return RiskCandidateView(bond_id=e.bond_id, isin=e.isin, secid=e.secid, as_of_date=e.as_of_date,
        market_source=e.market_source, status=status, reasons=tuple(sorted(set(reasons))),
        legal_issuer_id=f.legal_issuer_id, legal_issuer_source_issuer_id=f.legal_issuer_source_issuer_id,
        legal_issuer_inn=f.legal_issuer_inn, investment_score_v1=e.investment_score_v1,
        liquidity_score_v1=f.liquidity_score_v1, median_daily_turnover_value=f.median_daily_turnover_value,
        modified_duration_years=f.modified_duration_years, relative_price_sensitivity_per_1bp=f.relative_price_sensitivity_per_1bp,
        dv01_currency_per_bond=f.dv01_currency_per_bond, policy_max_position_weight=policy.max_position_weight,
        liquidity_capacity_amount_rub=capacity, source_evaluation=e)


def _provenance(batch):
    return RiskProvenance(as_of_date=batch.as_of_date, market_source=batch.market_source,
        candidate_bond_ids=batch.candidate_bond_ids, ready_evaluation_bond_ids=batch.normalization_bond_ids,
        legal_issuer_ids=tuple(sorted({e.candidate.features.legal_issuer_id for e in batch.evaluations
                                     if e.candidate.features.legal_issuer_id is not None})))


def validate_risk_batch(batch):
    _validated(batch, RiskCandidateBatchView)
    validate_investment_batch(batch.source_batch)
    _require((batch.as_of_date, batch.market_source, batch.candidate_bond_ids) ==
             (batch.source_batch.as_of_date, batch.source_batch.market_source, batch.source_batch.candidate_bond_ids))
    _require(batch.provenance == _provenance(batch.source_batch))
    _require(tuple(c.bond_id for c in batch.candidates) == batch.candidate_bond_ids)
    for c, e in zip(batch.candidates, batch.source_batch.evaluations):
        _require(c.model_dump() == _candidate(e, batch.policy).model_dump())
    for name, expected in (("candidate_count", len(batch.candidates)),
        ("eligible_count", sum(c.status == "ELIGIBLE" for c in batch.candidates)),
        ("blocked_count", sum(c.status == "BLOCKED" for c in batch.candidates)),
        ("investment_not_ready_count", sum(c.status == "INVESTMENT_MODEL_NOT_READY" for c in batch.candidates))):
        _require(type(getattr(batch, name)) is int and getattr(batch, name) == expected)


class RiskCandidateReducer:
    @staticmethod
    def build(investment_batch: InvestmentEvaluationBatchView) -> RiskCandidateBatchView:
        validate_investment_batch(investment_batch)
        policy = RiskEnginePolicyV1()
        candidates = tuple(_candidate(e, policy) for e in investment_batch.evaluations)
        return RiskCandidateBatchView(as_of_date=investment_batch.as_of_date, market_source=investment_batch.market_source,
            candidate_bond_ids=investment_batch.candidate_bond_ids, candidate_count=len(candidates),
            eligible_count=sum(c.status == "ELIGIBLE" for c in candidates), blocked_count=sum(c.status == "BLOCKED" for c in candidates),
            investment_not_ready_count=sum(c.status == "INVESTMENT_MODEL_NOT_READY" for c in candidates), candidates=candidates,
            source_batch=investment_batch, provenance=_provenance(investment_batch))
