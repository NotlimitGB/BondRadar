"""Pure identity-ordered projection of supplied CORE M3 evidence."""

from datetime import date, timedelta
from decimal import Decimal

from app.schemas.bond_m3_feature_view import BondM3FeatureView
from app.schemas.m3_audit_snapshot import M3AuditSnapshotItem, M3AuditSnapshotView
from app.schemas.m3_coverage_audit import M3CoverageAuditView
from app.schemas.unified_candidate import (
    UnifiedCandidateBatchView, UnifiedCandidateExclusion, UnifiedCandidateFeatures,
    UnifiedCandidateProvenance, UnifiedCandidateView,
)

CORE_REASONS = (
    ("all_supplied_evidence_consistent", "EVIDENCE_INVALID"),
    ("market_fresh", "MARKET_NOT_FRESH"),
    ("relative_value_ready", "RELATIVE_VALUE_NOT_READY"),
    ("has_credit_rating_evidence", "CREDIT_RATING_EVIDENCE_MISSING"),
    ("has_credit_comparable_cohort", "CREDIT_COMPARABLE_COHORT_MISSING"),
    ("liquidity_score_ready", "LIQUIDITY_SCORE_NOT_READY"),
    ("modified_duration_ready", "MODIFIED_DURATION_NOT_READY"),
    ("dv01_ready", "DV01_NOT_READY"),
    ("liquidity_relative_value_ready", "LIQUIDITY_RELATIVE_VALUE_NOT_READY"),
)
_CHILD_VERSIONS = {
    "market": "bond-market-feature-v1", "relative_value": "bond-relative-value-v1",
    "credit": "bond-credit-feature-v1", "credit_comparability": "bond-credit-comparability-v1",
    "liquidity": "bond-liquidity-feature-v1", "modified_duration": "bond-modified-duration-v1",
    "dv01": "bond-dv01-v1", "liquidity_relative_value": "bond-liquidity-aware-relative-value-v1",
}


def _require(condition):
    if not condition:
        raise ValueError("Inconsistent Unified Candidate source evidence")


def _positive_id(value):
    return type(value) is int and value > 0


def _ids(values, *, nonempty=False):
    _require(type(values) is list and (bool(values) or not nonempty))
    _require(all(_positive_id(v) for v in values))
    _require(values == sorted(set(values)))


def _count(value, expected):
    _require(type(value) is int and value == expected)


def _finite(value, *, minimum=None, maximum=None):
    _require(type(value) is Decimal and value.is_finite())
    _require(minimum is None or value >= minimum)
    _require(maximum is None or value <= maximum)


def _pair(snapshot_id, trade_date):
    _require(_positive_id(snapshot_id) and type(trade_date) is date)


def _availability(m3):
    a = m3.availability
    expected = {
        "all_supplied_evidence_consistent": m3.status == "CONSISTENT",
        "market_fresh": m3.market.market_status == "FRESH",
        "relative_value_ready": m3.relative_value.status == "READY",
        "has_credit_rating_evidence": (m3.credit.availability.has_bond_rating_event is True or
            m3.credit.availability.has_issuer_rating_event is True),
        "has_credit_comparable_cohort": m3.credit_comparability.availability.has_any_comparable_cohort is True,
        "liquidity_score_ready": m3.liquidity.score_status == "READY",
        "modified_duration_ready": m3.modified_duration.status == "READY",
        "dv01_ready": m3.dv01.status == "READY",
        "liquidity_relative_value_ready": m3.liquidity_relative_value.status == "READY",
        "has_peer_distribution_context": m3.peer_distribution is not None,
        "peer_distribution_ready": m3.peer_distribution is not None and m3.peer_distribution.status == "READY",
    }
    for name, value in expected.items():
        _require(type(getattr(a, name)) is bool and getattr(a, name) is value)
    for value in (m3.credit.availability.has_bond_rating_event,
                  m3.credit.availability.has_issuer_rating_event,
                  m3.credit_comparability.availability.has_any_comparable_cohort):
        _require(type(value) is bool)


def _core_integrity(m3):
    m, r, l, d, v, c = (m3.market, m3.relative_value, m3.liquidity,
                        m3.modified_duration, m3.dv01, m3.liquidity_relative_value)
    for child in (m, r, l, d, v, c, m3.credit, m3.credit_comparability):
        _require(type(child.bond_id) is int and child.bond_id == m3.bond_id)
        _require(type(child.as_of_date) is date and child.as_of_date == m3.as_of_date)
        if hasattr(child, "market_source"):
            _require(type(child.market_source) is str and child.market_source == m3.market_source)
    _require(m3.isin == m.isin and m3.secid == m.secid)
    for availability in (d.availability, v.availability):
        _require(all(value is True for value in availability.model_dump().values()))
    _require(l.availability.has_market_snapshots is True and l.availability.has_sufficient_observation_days is True and
             l.availability.has_score_components is True and l.availability.has_liquidity_score is True)
    _require(any(group.events and type(group.event_count) is int and group.event_count == len(group.events)
                 for group in m3.credit.bond_ratings + m3.credit.issuer_ratings))
    ready_entries = [entry for entry in m3.credit_comparability.bond_rating_entries +
                     m3.credit_comparability.issuer_rating_entries if entry.status == "READY"]
    _require(bool(ready_entries))
    for entry in ready_entries:
        key = entry.cohort_key
        _require(key is not None and key.target_kind == entry.target_kind and key.rating_agency == entry.rating_agency and
                 type(key.source_provider) is str and bool(key.source_provider.strip()) and
                 type(key.rating_value_raw) is str and bool(key.rating_value_raw.strip()) and
                 (key.rating_scale_raw is None or type(key.rating_scale_raw) is str))
        _require((key.source_provider, key.rating_scale_raw, key.rating_value_raw) ==
                 (entry.source_provider, entry.rating_scale_raw, entry.rating_value_raw))
    credit, comparison = m3.credit, m3.credit_comparability
    _require(comparison.provenance.credit_feature_contract_version == credit.contract_version and
             type(comparison.provenance.credit_feature_bond_id) is int and
             comparison.provenance.credit_feature_bond_id == m3.bond_id and
             comparison.provenance.credit_feature_as_of_date == m3.as_of_date)
    _require(comparison.provenance.bond_legal_issuer_profile_id == credit.provenance.bond_legal_issuer_profile_id)
    if credit.issuer_link_status == "VERIFIED":
        _require(credit.availability.has_verified_legal_issuer is True and _positive_id(credit.legal_issuer_id) and
                 comparison.provenance.legal_issuer_id == credit.legal_issuer_id)
    _pair(m.market_snapshot_id, m.market_trade_date)
    _require(m.provenance.market_snapshot_id == m.market_snapshot_id and
             m.provenance.market_trade_date == m.market_trade_date and
             m.provenance.market_source == m3.market_source)
    _require(r.curve.status == "READY" and type(r.curve.curve_trade_date) is date and
             r.curve.curve_trade_date == m.market_trade_date and
             r.curve.as_of_date == m3.as_of_date and r.curve.market_source == m3.market_source)
    _require(r.interpolation_method in {"EXACT_NODE", "LINEAR_INTERPOLATION"})
    _require((r.provenance.target_market_snapshot_id, r.provenance.target_market_trade_date,
              r.provenance.curve_trade_date) == (m.market_snapshot_id, m.market_trade_date, r.curve.curve_trade_date))
    for value in (m.yield_to_maturity_pct, r.target_yield_to_maturity_pct,
                  r.reference_ofz_yield_pct, r.spread_to_ofz_pp, r.spread_to_ofz_bps):
        _finite(value)
    for value in (m.duration_years, r.target_duration_years, d.macaulay_duration_years,
                  d.modified_duration_years, v.modified_duration_years,
                  v.relative_price_sensitivity_per_1bp, v.dv01_currency_per_bond, m.nkd):
        _finite(value, minimum=Decimal("0"))
    _require(r.target_yield_to_maturity_pct == m.yield_to_maturity_pct == d.yield_to_maturity_pct)
    _require(r.target_duration_years == m.duration_years == d.macaulay_duration_years)
    _require(d.modified_duration_years == v.modified_duration_years and v.modified_duration_status == "READY")
    if m.clean_price is not None:
        _finite(m.clean_price)
        _require(m.clean_price > 0)
    _finite(v.nominal_value)
    _require(v.nominal_value > 0 and v.currency_code == "RUB" and
             v.currency_state == v.nominal_state == "verified")
    _require(d.coupon_structure == "fixed" and d.perpetual_structure == "dated" and
             d.coupon_frequency_state == "verified" and _positive_id(d.coupon_frequency_per_year))
    for child in (d, v):
        _require((child.market_snapshot_id, child.market_trade_date) == (m.market_snapshot_id, m.market_trade_date))
        p = child.provenance
        _require(p.market_contract_version == "bond-market-feature-v1")
        _require((p.market_snapshot_id, p.market_trade_date, p.market_source, p.as_of_date) ==
                 (m.market_snapshot_id, m.market_trade_date, m3.market_source, m3.as_of_date))
    _require(_positive_id(d.provenance.security_master_profile_id) and
             d.provenance.security_master_profile_id == v.provenance.security_master_profile_id)
    _require(d.provenance.coupon_frequency_per_year == d.coupon_frequency_per_year and
             d.provenance.coupon_frequency_state == "verified")
    _require(v.provenance.modified_duration_contract_version == d.contract_version)
    for identity in (v.provenance.market_identity, v.provenance.modified_duration_market_identity):
        _require((identity.bond_id, identity.market_snapshot_id, identity.market_trade_date, identity.market_source) ==
                 (m3.bond_id, m.market_snapshot_id, m.market_trade_date, m3.market_source))
    _require(v.nkd_currency == m.nkd)
    for value in (l.liquidity_score_v1, l.score_components.turnover_percentile,
                  l.score_components.trade_count_percentile, l.score_components.recency_percentile):
        _finite(value, minimum=Decimal("0"), maximum=Decimal("100"))
    for value in (l.median_daily_turnover_value, l.median_daily_num_trades):
        _finite(value, minimum=Decimal("0"))
    if l.median_daily_trade_volume is not None:
        _finite(l.median_daily_trade_volume, minimum=Decimal("0"))
    p = l.provenance
    _require(type(p.selected_market_snapshot_ids) is list and type(p.selected_trade_dates) is list)
    _require(bool(p.selected_trade_dates) and len(p.selected_trade_dates) == len(p.selected_market_snapshot_ids))
    _require(all(_positive_id(i) for i in p.selected_market_snapshot_ids) and
             all(type(t) is date for t in p.selected_trade_dates) and
             all(a < b for a, b in zip(p.selected_trade_dates, p.selected_trade_dates[1:])))
    _require(p.market_source == m3.market_source and p.as_of_date == m3.as_of_date and
             type(p.window_start_date) is date and p.window_start_date == l.window_start_date)
    _require((p.selected_market_snapshot_ids[-1], p.selected_trade_dates[-1]) ==
             (m.market_snapshot_id, m.market_trade_date))
    _require(l.latest_trade_date == p.selected_trade_dates[-1] and
             type(l.latest_observation_age_days) is int and l.latest_observation_age_days >= 0)
    for field in ("reference_ofz_yield_pct", "spread_to_ofz_pp", "spread_to_ofz_bps", "interpolation_method"):
        _require(getattr(c, field) == getattr(r, field))
    _require(c.relative_value_status == r.status and c.liquidity_status == l.score_status and
             c.curve_trade_date == r.curve.curve_trade_date and c.liquidity_score_v1 == l.liquidity_score_v1)
    for field in ("turnover_percentile", "trade_count_percentile", "recency_percentile"):
        _require(getattr(c, field) == getattr(l.score_components, field))
    for identity in (c.provenance.relative_value_identity, c.provenance.liquidity_identity):
        _require((identity.bond_id, identity.as_of_date, identity.market_source) ==
                 (m3.bond_id, m3.as_of_date, m3.market_source))
    _require((m3.provenance.requested_bond_id, m3.provenance.requested_as_of_date,
              m3.provenance.requested_market_source) == (m3.bond_id, m3.as_of_date, m3.market_source))
    _require((m3.provenance.market_snapshot_id, m3.provenance.market_trade_date, m3.provenance.curve_trade_date) ==
             (m.market_snapshot_id, m.market_trade_date, r.curve.curve_trade_date))
    _require((c.provenance.relative_value_target_market_snapshot_id,
              c.provenance.relative_value_target_market_trade_date,
              c.provenance.liquidity_latest_market_snapshot_id, c.provenance.liquidity_latest_trade_date) ==
             (m.market_snapshot_id, m.market_trade_date, m.market_snapshot_id, m.market_trade_date))


def _project(snapshot, m3):
    m, r, l, d, v, credit = m3.market, m3.relative_value, m3.liquidity, m3.modified_duration, m3.dv01, m3.credit
    return UnifiedCandidateView(bond_id=m3.bond_id, isin=m3.isin, secid=m3.secid,
        as_of_date=m3.as_of_date, market_source=m3.market_source, m3=m3,
        features=UnifiedCandidateFeatures(
            market_snapshot_id=m.market_snapshot_id, market_trade_date=m.market_trade_date,
            clean_price=m.clean_price, nkd=m.nkd, yield_to_maturity_pct=m.yield_to_maturity_pct,
            maturity_date=m.maturity_date, days_to_maturity=m.days_to_maturity,
            reference_ofz_yield_pct=r.reference_ofz_yield_pct, spread_to_ofz_pp=r.spread_to_ofz_pp,
            spread_to_ofz_bps=r.spread_to_ofz_bps, curve_trade_date=r.curve.curve_trade_date,
            interpolation_method=r.interpolation_method, liquidity_score_v1=l.liquidity_score_v1,
            turnover_percentile=l.score_components.turnover_percentile,
            trade_count_percentile=l.score_components.trade_count_percentile,
            recency_percentile=l.score_components.recency_percentile,
            median_daily_turnover_value=l.median_daily_turnover_value,
            median_daily_trade_volume=l.median_daily_trade_volume, median_daily_num_trades=l.median_daily_num_trades,
            latest_liquidity_trade_date=l.latest_trade_date, latest_liquidity_observation_age_days=l.latest_observation_age_days,
            macaulay_duration_years=d.macaulay_duration_years, modified_duration_years=d.modified_duration_years,
            relative_price_sensitivity_per_1bp=v.relative_price_sensitivity_per_1bp,
            dv01_currency_per_bond=v.dv01_currency_per_bond, currency_code=v.currency_code,
            nominal_value=v.nominal_value, coupon_structure=d.coupon_structure,
            coupon_frequency_per_year=d.coupon_frequency_per_year, legal_issuer_id=credit.legal_issuer_id,
            legal_issuer_source_issuer_id=credit.legal_issuer_source_issuer_id, legal_issuer_inn=credit.legal_issuer_inn),
        provenance=UnifiedCandidateProvenance(
            source_m3_snapshot_contract_version=snapshot.contract_version,
            source_m3_composite_contract_version=m3.contract_version,
            requested_as_of_date=snapshot.as_of_date, requested_market_source=snapshot.market_source,
            market_snapshot_id=m.market_snapshot_id, market_trade_date=m.market_trade_date,
            curve_trade_date=r.curve.curve_trade_date,
            bond_legal_issuer_profile_id=credit.provenance.bond_legal_issuer_profile_id,
            legal_issuer_id=credit.legal_issuer_id, security_master_profile_id=d.provenance.security_master_profile_id,
            liquidity_window_start_date=l.provenance.window_start_date,
            liquidity_selected_market_snapshot_ids=tuple(l.provenance.selected_market_snapshot_ids)))


class UnifiedCandidateReducer:
    @staticmethod
    def build(snapshot: M3AuditSnapshotView) -> UnifiedCandidateBatchView:
        try:
            return UnifiedCandidateReducer._build(snapshot)
        except (AttributeError, TypeError, KeyError):
            raise ValueError("Malformed Unified Candidate source evidence") from None

    @staticmethod
    def _build(snapshot: M3AuditSnapshotView) -> UnifiedCandidateBatchView:
        if type(snapshot) is not M3AuditSnapshotView:
            raise ValueError("snapshot must be an exact M3AuditSnapshotView")
        _require(snapshot.contract_version == "m3-audit-snapshot-v1" and snapshot.pit_ready is False)
        _require(type(snapshot.as_of_date) is date and type(snapshot.market_source) is str and snapshot.market_source == "moex")
        for value in (snapshot.max_market_age_days, snapshot.max_curve_age_days):
            _require(type(value) is int and value >= 0)
        _require(_positive_id(snapshot.liquidity_lookback_calendar_days) and _positive_id(snapshot.liquidity_min_observation_days) and
                 snapshot.liquidity_min_observation_days <= snapshot.liquidity_lookback_calendar_days)
        try:
            snapshot.as_of_date - timedelta(days=snapshot.liquidity_lookback_calendar_days - 1)
        except OverflowError:
            raise ValueError("Invalid snapshot window") from None
        requested, existing, missing = snapshot.requested_bond_ids, snapshot.existing_bond_ids, snapshot.missing_bond_ids
        for values in (requested, existing, missing):
            _ids(values, nonempty=values is requested)
        _require(not set(existing) & set(missing) and sorted(existing + missing) == requested)
        for name, ids in (("requested", requested), ("existing", existing), ("missing", missing)):
            _count(getattr(snapshot, name + "_bond_count"), len(ids))
            _ids(getattr(snapshot.provenance, name + "_bond_ids"))
            _require(getattr(snapshot.provenance, name + "_bond_ids") == ids)
        _require(snapshot.provenance.task285_batch_contract_version == "bond-liquidity-batch-feature-v1" and
                 snapshot.provenance.task282_contract_version == "bond-m3-feature-view-v1" and
                 snapshot.provenance.task283_audit_contract_version == "m3-coverage-audit-v1" and
                 snapshot.provenance.audit_denominator == "EXISTING_REQUESTED_BONDS" and
                 snapshot.provenance.peer_context_mode == "NOT_BUILT")
        status = "NO_EXISTING_BONDS" if not existing else "PARTIAL" if missing else "COMPLETE"
        _require(snapshot.status == status)
        _require(type(snapshot.items) is list and len(snapshot.items) == len(requested))
        composites = []
        for bond_id, item in zip(requested, snapshot.items, strict=True):
            _require(type(item) is M3AuditSnapshotItem and type(item.bond_id) is int and item.bond_id == bond_id)
            if bond_id in missing:
                _require(item.build_status == "BOND_NOT_FOUND" and item.composite is None)
                continue
            m3 = item.composite
            _require(item.build_status == "BUILT" and type(m3) is BondM3FeatureView)
            _require(m3.contract_version == "bond-m3-feature-view-v1" and m3.pit_ready is False and
                     type(m3.bond_id) is int and m3.bond_id == bond_id and type(m3.as_of_date) is date and
                     m3.as_of_date == snapshot.as_of_date and type(m3.market_source) is str and m3.market_source == snapshot.market_source)
            _require(m3.status in {"CONSISTENT", "EVIDENCE_INVALID"})
            for name, version in _CHILD_VERSIONS.items():
                child = getattr(m3, name)
                _require(type(child) is BondM3FeatureView.model_fields[name].annotation and
                         child.contract_version == version and child.pit_ready is False)
            _require(m3.relative_value.curve.contract_version == "ofz-reference-curve-v1" and m3.relative_value.curve.pit_ready is False)
            _availability(m3)
            composites.append(m3)
        _count(snapshot.composite_count, len(composites))
        consistent = sum(m3.status == "CONSISTENT" for m3 in composites)
        _count(snapshot.consistent_composite_count, consistent)
        _count(snapshot.invalid_composite_count, len(composites) - consistent)
        core = [m3.bond_id for m3 in composites if all(getattr(m3.availability, name) for name, _ in CORE_REASONS)]
        audit = snapshot.coverage_audit
        if not existing:
            _require(audit is None)
        else:
            _require(type(audit) is M3CoverageAuditView and audit.contract_version == "m3-coverage-audit-v1" and audit.pit_ready is False)
            _require(type(audit.as_of_date) is date and audit.as_of_date == snapshot.as_of_date and audit.market_source == snapshot.market_source)
            _ids(audit.bond_ids)
            _ids(audit.provenance.bond_ids)
            _require(audit.bond_ids == existing and audit.provenance.bond_ids == existing and
                     audit.provenance.as_of_date == snapshot.as_of_date and audit.provenance.market_source == snapshot.market_source and
                     audit.provenance.core_completeness_definition == "CORE_M3_COMPLETE_V1" and
                     audit.provenance.source_contract_version == "bond-m3-feature-view-v1")
            _count(audit.universe_size, len(existing))
            _count(audit.provenance.universe_size, len(existing))
            _count(audit.consistent_composite_count, consistent)
            _count(audit.invalid_composite_count, len(existing) - consistent)
            _ids(audit.core_m3_complete_bond_ids)
            _ids(audit.core_m3_incomplete_bond_ids)
            _require(audit.core_m3_complete_bond_ids == core and
                     audit.core_m3_incomplete_bond_ids == [i for i in existing if i not in core])
            _count(audit.core_m3_complete_count, len(core))
        candidates, exclusions = [], []
        for item in snapshot.items:
            m3 = item.composite
            if item.bond_id in core:
                _core_integrity(m3)
                candidates.append(_project(snapshot, m3))
            else:
                reasons = ("BOND_NOT_FOUND",) if m3 is None else tuple(sorted(reason for name, reason in CORE_REASONS if not getattr(m3.availability, name)))
                exclusions.append(UnifiedCandidateExclusion(bond_id=item.bond_id, build_status=item.build_status,
                    isin=None if m3 is None else m3.isin, secid=None if m3 is None else m3.secid,
                    m3_status=None if m3 is None else m3.status, reasons=reasons))
        return UnifiedCandidateBatchView(status=status, as_of_date=snapshot.as_of_date, market_source=snapshot.market_source,
            max_market_age_days=snapshot.max_market_age_days, max_curve_age_days=snapshot.max_curve_age_days,
            liquidity_lookback_calendar_days=snapshot.liquidity_lookback_calendar_days,
            liquidity_min_observation_days=snapshot.liquidity_min_observation_days,
            requested_bond_ids=tuple(requested), existing_bond_ids=tuple(existing), missing_bond_ids=tuple(missing),
            candidate_bond_ids=tuple(core), excluded_bond_ids=tuple(i.bond_id for i in exclusions),
            requested_bond_count=len(requested), existing_bond_count=len(existing), missing_bond_count=len(missing),
            candidate_count=len(candidates), exclusion_count=len(exclusions), candidates=tuple(candidates), exclusions=tuple(exclusions))
