"""Fail-closed scoring of supplied CORE candidates and already-built peer evidence."""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal, Context, ROUND_HALF_EVEN, localcontext

from app.schemas.unified_candidate import UnifiedCandidateBatchView, UnifiedCandidateView
from app.schemas.investment_model import (
    InvestmentPeerContext, InvestmentModelPolicyV1, InvestmentEvaluationView,
    InvestmentEvaluationBatchView, InvestmentProvenance,
)
from app.services.investment_model_math import midrank_percentile

_VERSIONS = {
    "market": "bond-market-feature-v1", "relative_value": "bond-relative-value-v1",
    "credit": "bond-credit-feature-v1", "credit_comparability": "bond-credit-comparability-v1",
    "liquidity": "bond-liquidity-feature-v1", "modified_duration": "bond-modified-duration-v1",
    "dv01": "bond-dv01-v1", "liquidity_relative_value": "bond-liquidity-aware-relative-value-v1",
}


def _require(condition):
    if not condition:
        raise ValueError("Inconsistent Investment Model input evidence")


def _finite(value, minimum=None, maximum=None):
    _require(type(value) is Decimal and value.is_finite())
    _require(minimum is None or value >= minimum)
    _require(maximum is None or value <= maximum)


def _strict(model, expected):
    _require(type(model) is expected)
    # Revalidation catches model_copy/model_construct bypasses before arithmetic.
    try:
        expected.model_validate(model.model_dump(), strict=True)
    except (ValueError, TypeError):
        raise ValueError("Invalid Investment Model input contract") from None
    def pit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "pit_ready":
                    _require(item is False)
                pit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                pit(item)
    pit(model.model_dump())


def _ids(values):
    _require(type(values) is tuple and all(type(i) is int and i > 0 for i in values))
    _require(values == tuple(sorted(set(values))))


def selectors(candidate):
    credit = candidate.m3.credit_comparability
    return tuple(sorted({(e.target_kind, e.rating_agency)
                         for e in credit.bond_rating_entries + credit.issuer_rating_entries}))


def _entry(candidate, kind, agency):
    credit = candidate.m3.credit_comparability
    family = credit.bond_rating_entries if kind == "BOND" else credit.issuer_rating_entries
    rows = [e for e in family if e.rating_agency == agency]
    return rows[0] if len(rows) == 1 and rows[0].status == "READY" and credit.status == "READY" else None


def validate_candidate_batch(batch):
    _strict(batch, UnifiedCandidateBatchView)
    _require(type(batch.as_of_date) is date and batch.market_source == "moex")
    _require(batch.contract_version == "unified-candidate-batch-v1" and batch.pit_ready is False)
    for name in ("requested", "existing", "missing", "candidate", "excluded"):
        ids = getattr(batch, name + "_bond_ids")
        _ids(ids)
        count_name = "candidate_count" if name == "candidate" else "exclusion_count" if name == "excluded" else name + "_bond_count"
        _require(type(getattr(batch, count_name)) is int and getattr(batch, count_name) == len(ids))
    _require(bool(batch.requested_bond_ids))
    existing, missing = set(batch.existing_bond_ids), set(batch.missing_bond_ids)
    _require(not existing & missing and existing | missing == set(batch.requested_bond_ids))
    _require(not set(batch.candidate_bond_ids) & set(batch.excluded_bond_ids) and
             set(batch.candidate_bond_ids) | set(batch.excluded_bond_ids) == set(batch.requested_bond_ids))
    _require(set(batch.candidate_bond_ids) <= existing)
    _require(batch.status == ("NO_EXISTING_BONDS" if not existing else "PARTIAL" if missing else "COMPLETE"))
    _require(tuple(c.bond_id for c in batch.candidates) == batch.candidate_bond_ids)
    _require(tuple(e.bond_id for e in batch.exclusions) == batch.excluded_bond_ids)
    for e in batch.exclusions:
        _require(e.build_status == ("BOND_NOT_FOUND" if e.bond_id in missing else "BUILT"))
        _require(bool(e.reasons) and e.reasons == tuple(sorted(set(e.reasons))))
        if e.bond_id in missing:
            _require(e.reasons == ("BOND_NOT_FOUND",) and e.isin is e.secid is e.m3_status is None)
    for name in ("max_market_age_days", "max_curve_age_days"):
        _require(type(getattr(batch, name)) is int and getattr(batch, name) >= 0)
    _require(type(batch.liquidity_lookback_calendar_days) is int and type(batch.liquidity_min_observation_days) is int and
             0 < batch.liquidity_min_observation_days <= batch.liquidity_lookback_calendar_days)
    for c in batch.candidates:
        _validate_candidate(c, batch)


def _validate_candidate(c, batch):
    _require(type(c) is UnifiedCandidateView)
    m3, f, p = c.m3, c.features, c.provenance
    _require(c.contract_version == "unified-candidate-v1" and c.eligibility == "CORE_M3_COMPLETE_V1")
    _require((c.as_of_date, c.market_source) == (batch.as_of_date, batch.market_source))
    _require((m3.bond_id, m3.as_of_date, m3.market_source, m3.isin, m3.secid) ==
             (c.bond_id, c.as_of_date, c.market_source, c.isin, c.secid))
    _require(m3.status == "CONSISTENT")
    for name in ("all_supplied_evidence_consistent", "market_fresh", "relative_value_ready", "has_credit_rating_evidence",
                 "has_credit_comparable_cohort", "liquidity_score_ready", "modified_duration_ready", "dv01_ready",
                 "liquidity_relative_value_ready"):
        _require(getattr(m3.availability, name) is True)
    _require(m3.availability.has_peer_distribution_context is (m3.peer_distribution is not None) and
             m3.availability.peer_distribution_ready is (m3.peer_distribution is not None and m3.peer_distribution.status == "READY"))
    for name, version in _VERSIONS.items():
        child = getattr(m3, name)
        _require(child.contract_version == version and child.pit_ready is False)
        _require(type(child.bond_id) is int and child.bond_id == c.bond_id and type(child.as_of_date) is date and child.as_of_date == c.as_of_date)
        if hasattr(child, "market_source"):
            _require(child.market_source == c.market_source)
    m, r, l, d, v, credit = m3.market, m3.relative_value, m3.liquidity, m3.modified_duration, m3.dv01, m3.credit
    _require(m.market_status == "FRESH" and r.status == l.score_status == d.status == v.status == m3.liquidity_relative_value.status == "READY")
    _require(credit.availability.has_bond_rating_event is True or credit.availability.has_issuer_rating_event is True)
    _require(m3.credit_comparability.availability.has_any_comparable_cohort is True)
    _require(all(value is True for child in (d, v) for value in child.availability.model_dump().values()))
    _require(l.availability.has_market_snapshots is True and l.availability.has_sufficient_observation_days is True and
             l.availability.has_score_components is True and l.availability.has_liquidity_score is True)
    _require(r.curve.contract_version == "ofz-reference-curve-v1" and r.curve.status == "READY" and r.curve.pit_ready is False)
    expected = {name: getattr(m, name) for name in ("market_snapshot_id", "market_trade_date", "clean_price", "nkd", "yield_to_maturity_pct", "maturity_date", "days_to_maturity")}
    expected.update({name: getattr(r, name) for name in ("reference_ofz_yield_pct", "spread_to_ofz_pp", "spread_to_ofz_bps", "interpolation_method")})
    expected["curve_trade_date"] = r.curve.curve_trade_date
    expected.update({name: getattr(l, name) for name in ("liquidity_score_v1", "median_daily_turnover_value", "median_daily_trade_volume", "median_daily_num_trades")})
    expected.update({name: getattr(l.score_components, name) for name in ("turnover_percentile", "trade_count_percentile", "recency_percentile")})
    expected.update(latest_liquidity_trade_date=l.latest_trade_date, latest_liquidity_observation_age_days=l.latest_observation_age_days)
    expected.update({name: getattr(d, name) for name in ("macaulay_duration_years", "modified_duration_years", "coupon_structure", "coupon_frequency_per_year")})
    expected.update({name: getattr(v, name) for name in ("relative_price_sensitivity_per_1bp", "dv01_currency_per_bond", "currency_code", "nominal_value")})
    expected.update({name: getattr(credit, name) for name in ("legal_issuer_id", "legal_issuer_source_issuer_id", "legal_issuer_inn")})
    _require(all(getattr(f, name) == value for name, value in expected.items()))
    for name in ("yield_to_maturity_pct", "reference_ofz_yield_pct", "spread_to_ofz_pp", "spread_to_ofz_bps"):
        _finite(getattr(f, name))
    for name in ("macaulay_duration_years", "modified_duration_years", "dv01_currency_per_bond", "relative_price_sensitivity_per_1bp", "nkd", "median_daily_turnover_value", "median_daily_num_trades"):
        _finite(getattr(f, name), Decimal("0"))
    _finite(f.nominal_value)
    _require(f.nominal_value > 0 and type(f.coupon_frequency_per_year) is int and f.coupon_frequency_per_year > 0)
    for name in ("liquidity_score_v1", "turnover_percentile", "trade_count_percentile", "recency_percentile"):
        _finite(getattr(f, name), Decimal("0"), Decimal("100"))
    _require(type(f.market_snapshot_id) is int and f.market_snapshot_id > 0 and type(f.market_trade_date) is date)
    _require(f.curve_trade_date == f.market_trade_date)
    _require((p.requested_as_of_date, p.requested_market_source, p.market_snapshot_id, p.market_trade_date, p.curve_trade_date) ==
             (c.as_of_date, c.market_source, f.market_snapshot_id, f.market_trade_date, f.curve_trade_date))
    _require(p.security_master_profile_id == d.provenance.security_master_profile_id == v.provenance.security_master_profile_id)
    _require(type(p.security_master_profile_id) is int and p.security_master_profile_id > 0)
    _require(d.provenance.coupon_frequency_per_year == f.coupon_frequency_per_year and d.provenance.coupon_frequency_state == "verified")
    _require(p.liquidity_selected_market_snapshot_ids == tuple(l.provenance.selected_market_snapshot_ids))
    _require(bool(p.liquidity_selected_market_snapshot_ids) and p.liquidity_selected_market_snapshot_ids[-1] == f.market_snapshot_id)
    _require(len(l.provenance.selected_trade_dates) == len(p.liquidity_selected_market_snapshot_ids) and
             all(type(t) is date for t in l.provenance.selected_trade_dates) and
             all(a < b for a, b in zip(l.provenance.selected_trade_dates, l.provenance.selected_trade_dates[1:])))
    _require(l.provenance.selected_trade_dates[-1] == f.market_trade_date)
    _require(p.liquidity_window_start_date == l.provenance.window_start_date and
             (l.provenance.as_of_date, l.provenance.market_source) == (c.as_of_date, c.market_source))
    _require(p.bond_legal_issuer_profile_id == credit.provenance.bond_legal_issuer_profile_id and
             p.legal_issuer_id == credit.legal_issuer_id)
    _require((m.provenance.market_snapshot_id, m.provenance.market_trade_date, m.provenance.market_source) ==
             (f.market_snapshot_id, f.market_trade_date, c.market_source))
    _require((r.provenance.target_market_snapshot_id, r.provenance.target_market_trade_date, r.provenance.curve_trade_date) ==
             (f.market_snapshot_id, f.market_trade_date, f.curve_trade_date))
    _require((r.curve.as_of_date, r.curve.market_source) == (c.as_of_date, c.market_source))
    _require(r.target_yield_to_maturity_pct == m.yield_to_maturity_pct == d.yield_to_maturity_pct and
             r.target_duration_years == m.duration_years == d.macaulay_duration_years)
    _require(v.modified_duration_years == d.modified_duration_years and v.modified_duration_status == "READY")
    _require(d.coupon_frequency_state == "verified" and d.perpetual_structure == "dated" and
             v.currency_state == v.nominal_state == "verified")
    for child in (d, v):
        _require((child.market_snapshot_id, child.market_trade_date) == (f.market_snapshot_id, f.market_trade_date))
        _require((child.provenance.market_snapshot_id, child.provenance.market_trade_date,
                  child.provenance.market_source, child.provenance.as_of_date) ==
                 (f.market_snapshot_id, f.market_trade_date, c.market_source, c.as_of_date))
    for identity in (v.provenance.market_identity, v.provenance.modified_duration_market_identity):
        _require((identity.bond_id, identity.market_snapshot_id, identity.market_trade_date, identity.market_source) ==
                 (c.bond_id, f.market_snapshot_id, f.market_trade_date, c.market_source))
    lr = m3.liquidity_relative_value
    _require(lr.relative_value_status == r.status and lr.liquidity_status == l.score_status)
    for name in ("spread_to_ofz_pp", "spread_to_ofz_bps", "reference_ofz_yield_pct", "interpolation_method"):
        _require(getattr(lr, name) == getattr(r, name))
    _require(lr.liquidity_score_v1 == l.liquidity_score_v1)
    for name in ("turnover_percentile", "trade_count_percentile", "recency_percentile"):
        _require(getattr(lr, name) == getattr(l.score_components, name))
    for identity in (lr.provenance.relative_value_identity, lr.provenance.liquidity_identity):
        _require((identity.bond_id, identity.as_of_date, identity.market_source) == (c.bond_id, c.as_of_date, c.market_source))
    comparison = m3.credit_comparability
    _require((comparison.provenance.credit_feature_contract_version, comparison.provenance.credit_feature_bond_id,
              comparison.provenance.credit_feature_as_of_date) == (credit.contract_version, c.bond_id, c.as_of_date))
    for kind, family in (("BOND", m3.credit_comparability.bond_rating_entries), ("LEGAL_ISSUER", m3.credit_comparability.issuer_rating_entries)):
        for e in family:
            _require(e.target_kind == kind)
            if e.status == "READY":
                k = e.cohort_key
                _require(type(e.event_count) is int and e.event_count == 1 and len(e.event_ids) == 1 and
                         type(e.selected_event_id) is int and e.selected_event_id > 0 and e.event_ids[0] == e.selected_event_id)
                _require(k is not None and (k.target_kind, k.rating_agency, k.source_provider, k.rating_scale_raw, k.rating_value_raw) ==
                         (kind, e.rating_agency, e.source_provider, e.rating_scale_raw, e.rating_value_raw))
                _require(type(k.source_provider) is str and bool(k.source_provider.strip()) and type(k.rating_value_raw) is str and bool(k.rating_value_raw.strip()))


def _validate_context(ctx, candidates):
    _strict(ctx, InvestmentPeerContext)
    _require(ctx.bond_id in candidates)
    c = candidates[ctx.bond_id]
    m, d = ctx.member, ctx.distribution
    _require((ctx.target_kind, ctx.rating_agency) in selectors(c))
    _require(m.contract_version == "bond-credit-cohort-relative-value-member-v1")
    _require((m.bond_id, m.as_of_date, m.market_source, m.requested_target_kind, m.requested_rating_agency) ==
             (c.bond_id, c.as_of_date, c.market_source, ctx.target_kind, ctx.rating_agency))
    _require(d.contract_version == "credit-cohort-peer-spread-distribution-v1" and d.min_peer_count == 2 and type(d.min_peer_count) is int)
    _require(d.candidate_count == d.provenance.candidate_count == len(candidates))
    _require(type(d.candidate_count) is int and type(d.eligible_peer_count) is int)
    _require(d.provenance.min_peer_count == 2 and d.provenance.target_member_contract_version == m.contract_version)
    _require((d.target_bond_id, d.as_of_date, d.market_source) == (c.bond_id, c.as_of_date, c.market_source))
    _require(d.provenance.target_market_snapshot_id == m.market_snapshot_id == c.features.market_snapshot_id)
    _require(d.provenance.target_rating_event_id == m.rating_event_id)
    _require(d.cohort_key == m.cohort_key and d.target_spread_to_ofz_bps == m.spread_to_ofz_bps == c.features.spread_to_ofz_bps)
    _require(m.market_trade_date == c.features.market_trade_date)
    _require((m.provenance.target_market_snapshot_id, m.provenance.target_market_trade_date,
              m.provenance.curve_trade_date, m.provenance.as_of_date, m.provenance.market_source) ==
             (c.features.market_snapshot_id, c.features.market_trade_date, c.features.curve_trade_date, c.as_of_date, c.market_source))
    _require((m.provenance.credit_identity.bond_id, m.provenance.credit_identity.as_of_date) == (c.bond_id, c.as_of_date))
    _require((m.provenance.relative_value_identity.bond_id, m.provenance.relative_value_identity.as_of_date,
              m.provenance.relative_value_identity.market_source) == (c.bond_id, c.as_of_date, c.market_source))
    _require(m.provenance.credit_comparability_contract_version == "bond-credit-comparability-v1" and
             m.provenance.relative_value_contract_version == "bond-relative-value-v1" and
             m.provenance.ofz_curve_contract_version == "ofz-reference-curve-v1")
    _require((m.target_yield_to_maturity_pct, m.target_duration_years, m.reference_ofz_yield_pct,
              m.spread_to_ofz_pp, m.interpolation_method, m.curve_trade_date) ==
             (c.features.yield_to_maturity_pct, c.features.macaulay_duration_years, c.features.reference_ofz_yield_pct,
              c.features.spread_to_ofz_pp, c.features.interpolation_method, c.features.curve_trade_date))
    expected_peers = []
    entry = _entry(c, ctx.target_kind, ctx.rating_agency)
    _require((m.status == "READY") is (entry is not None))
    if m.status == "READY":
        _require(entry is not None and m.cohort_key == entry.cohort_key and m.rating_event_id == entry.selected_event_id)
        _require((m.rating_event_date, m.rating_source_provider, m.rating_scale_raw, m.rating_value_raw) ==
                 (entry.latest_event_date, entry.source_provider, entry.rating_scale_raw, entry.rating_value_raw))
        _require(all(value is True for value in m.availability.model_dump().values()))
        for other in candidates.values():
            e = _entry(other, ctx.target_kind, ctx.rating_agency)
            if other.bond_id != c.bond_id and e is not None and e.cohort_key == m.cohort_key:
                expected_peers.append(other.bond_id)
        _require(d.status == ("NO_ELIGIBLE_PEERS" if not expected_peers else "INSUFFICIENT_PEERS" if len(expected_peers) < 2 else "READY"))
    else:
        _require(d.status == "TARGET_MEMBER_INVALID")
    actual = [row.bond_id for row in d.eligible_peers]
    _require(actual == sorted(expected_peers) and d.eligible_peer_count == len(actual) and d.provenance.eligible_peer_bond_ids == actual)
    _require(not d.exclusions.duplicate_peer_bond_ids and d.exclusions.invalid_candidate_count == 0)
    counts = [value for name, value in d.exclusions.model_dump().items() if name.endswith("_count")]
    _require(all(type(value) is int and value >= 0 for value in counts) and sum(counts) + len(actual) == len(candidates))
    for row in d.eligible_peers:
        peer = candidates[row.bond_id]
        e = _entry(peer, ctx.target_kind, ctx.rating_agency)
        _require((row.isin, row.secid, row.spread_to_ofz_bps, row.market_snapshot_id, row.rating_event_id) ==
                 (peer.isin, peer.secid, peer.features.spread_to_ofz_bps, peer.features.market_snapshot_id, e.selected_event_id))
    has_stats = d.status in ("READY", "INSUFFICIENT_PEERS")
    for name in ("peer_min_spread_bps", "peer_median_spread_bps", "peer_mean_spread_bps", "peer_max_spread_bps", "spread_minus_peer_median_bps"):
        if has_stats:
            _finite(getattr(d, name))
        else:
            _require(getattr(d, name) is None)
    if d.status == "READY":
        _finite(d.target_spread_percentile, Decimal("0"), Decimal("100"))
    else:
        _require(d.target_spread_percentile is None)
    _require(d.availability.has_ready_peer_distribution is (d.status == "READY") and
             d.availability.has_target_spread_percentile is (d.status == "READY"))
    expected_availability = dict(has_valid_target=m.status == "READY", has_eligible_peers=bool(actual),
        has_minimum_peer_count=d.status == "READY", has_peer_distribution=has_stats, has_peer_median=has_stats,
        has_target_spread_percentile=d.status == "READY", has_spread_vs_peer_median=has_stats,
        has_ready_peer_distribution=d.status == "READY")
    _require(all(getattr(d.availability, name) is value for name, value in expected_availability.items()))


def _context_order(ctx):
    key = ctx.member.cohort_key
    return (ctx.target_kind, ctx.rating_agency, key.source_provider if key else "",
            key.rating_scale_raw is not None if key else False, key.rating_scale_raw or "" if key else "",
            key.rating_value_raw if key else "")


def _rank_ready(evaluations):
    # Decimal comparisons are exact and require no arithmetic or caller context.
    return sorted((e for e in evaluations if e.status == "READY"), reverse=True,
                  key=lambda e: (e.investment_score_v1, e.credit_cohort_relative_value_score,
                                 e.ofz_relative_value_score, e.liquidity_score, e.duration_score, -e.bond_id))


class InvestmentModelReducer:
    @staticmethod
    def build(batch, peer_contexts):
        validate_candidate_batch(batch)
        _require(isinstance(peer_contexts, Sequence) and not isinstance(peer_contexts, (str, bytes, bytearray)))
        contexts = tuple(peer_contexts)
        candidates = {c.bond_id: c for c in batch.candidates}
        for ctx in contexts:
            _validate_context(ctx, candidates)
        keys = [(ctx.bond_id, ctx.target_kind, ctx.rating_agency) for ctx in contexts]
        expected = {(c.bond_id, kind, agency) for c in batch.candidates for kind, agency in selectors(c)}
        _require(len(keys) == len(set(keys)) and set(keys) == expected)
        grouped = {i: tuple(sorted((ctx for ctx in contexts if ctx.bond_id == i), key=_context_order)) for i in candidates}
        ready = {i: tuple(ctx for ctx in grouped[i] if ctx.distribution.status == "READY") for i in candidates}
        selected = {i: min(rows, key=lambda ctx: (ctx.distribution.target_spread_percentile, _context_order(ctx))) for i, rows in ready.items() if rows}
        normalization_ids = tuple(sorted(selected))
        spreads = tuple(candidates[i].features.spread_to_ofz_bps for i in normalization_ids)
        durations = tuple(candidates[i].features.modified_duration_years for i in normalization_ids)
        policy = InvestmentModelPolicyV1()
        evaluations = []
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            for i, c in candidates.items():
                f, chosen = c.features, selected.get(i)
                ofz = duration_pct = duration = credit = total = None
                if chosen is not None:
                    ofz = midrank_percentile(f.spread_to_ofz_bps, spreads)
                    duration_pct = midrank_percentile(f.modified_duration_years, durations)
                    duration = Decimal("100") - duration_pct
                    credit = chosen.distribution.target_spread_percentile
                    total = policy.ofz_relative_value_weight * ofz + policy.credit_cohort_relative_value_weight * credit + policy.liquidity_weight * f.liquidity_score_v1 + policy.duration_weight * duration
                evaluations.append(InvestmentEvaluationView(
                    bond_id=i, isin=c.isin, secid=c.secid, as_of_date=c.as_of_date, market_source=c.market_source,
                    status="READY" if chosen else "CREDIT_PEER_CONTEXT_UNAVAILABLE", investment_score_v1=total,
                    ofz_relative_value_percentile=ofz, ofz_relative_value_score=ofz,
                    credit_cohort_relative_value_score=credit, liquidity_score=f.liquidity_score_v1,
                    duration_percentile=duration_pct, duration_score=duration,
                    yield_to_maturity_pct=f.yield_to_maturity_pct, spread_to_ofz_bps=f.spread_to_ofz_bps,
                    reference_ofz_yield_pct=f.reference_ofz_yield_pct, modified_duration_years=f.modified_duration_years,
                    dv01_currency_per_bond=f.dv01_currency_per_bond, liquidity_score_v1=f.liquidity_score_v1,
                    attempted_credit_contexts=grouped[i], ready_credit_contexts=ready[i], selected_credit_context=chosen,
                    rank=None, quality_flags=() if chosen else ("CREDIT_PEER_CONTEXT_UNAVAILABLE",), candidate=c,
                    provenance=InvestmentProvenance(batch_candidate_bond_ids=batch.candidate_bond_ids,
                        batch_ready_bond_ids=normalization_ids, target_market_snapshot_id=f.market_snapshot_id,
                        curve_trade_date=f.curve_trade_date, m3_as_of_date=c.as_of_date,
                        peer_distribution_contract_versions=tuple(sorted({ctx.distribution.contract_version for ctx in grouped[i]})))) )
        ranked = _rank_ready(evaluations)
        ranks = {e.bond_id: rank for rank, e in enumerate(ranked, 1)}
        scores = [e.investment_score_v1 for e in ranked]
        return InvestmentEvaluationBatchView(as_of_date=batch.as_of_date, market_source=batch.market_source,
            candidate_count=len(candidates), ready_count=len(ranked), unavailable_count=len(candidates)-len(ranked),
            candidate_bond_ids=batch.candidate_bond_ids, normalization_bond_ids=normalization_ids,
            ranked_bond_ids=tuple(e.bond_id for e in ranked), score_min=min(scores) if scores else None,
            score_max=max(scores) if scores else None,
            evaluations=tuple(e.model_copy(update={"rank": ranks.get(e.bond_id)}) for e in evaluations), source_batch=batch)
