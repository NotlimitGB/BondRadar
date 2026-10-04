"""Pure frozen-contract checks, canonical hashes and experiment verdicts."""
from datetime import date, datetime, timedelta
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
import hashlib
import json
from pydantic import BaseModel
from app.schemas.shadow_experiment import (
    ShadowExperimentPolicyV1, OfzBenchmarkGenesisViewV1, ShadowExperimentGenesisPlanV1,
    StrategyExperimentObservationV1, OfzBenchmarkDailyViewV1, ShadowExperimentComparisonViewV1,
)

DECIMAL_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)
ZERO = Decimal("0")
ONE = Decimal("1")


def require(condition, code="INPUT_EVIDENCE_INVALID"):
    if not condition:
        raise ValueError(code)


def finite(value, *, positive=False):
    return type(value) is Decimal and value.is_finite() and (value > 0 if positive else True)


def strict(value, cls):
    require(type(value) is cls)
    try:
        cls.model_validate(value.model_dump(), strict=True)
        require(cls.model_validate_json(value.model_dump_json()).model_dump() == value.model_dump())
        def inspect(obj):
            if isinstance(obj, BaseModel):
                require(set(obj.__dict__) <= set(type(obj).model_fields))
                for name in type(obj).model_fields:
                    if name == "pit_ready": require(getattr(obj, name) is False)
                    inspect(getattr(obj, name))
            elif isinstance(obj, (tuple, list)):
                for item in obj: inspect(item)
        inspect(value)
    except Exception:
        raise ValueError("INPUT_EVIDENCE_INVALID") from None


def canonical(value):
    if isinstance(value, BaseModel): return canonical(value.model_dump())
    if type(value) is Decimal:
        require(value.is_finite())
        if not value: return "0"
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if value.as_tuple().exponent < 0 else text
    if isinstance(value, (date, datetime)): return value.isoformat()
    if isinstance(value, dict):
        forbidden = {"password", "token", "access_token", "refresh_token", "api_key", "authorization", "headers", "cookies", "secret", "connection_string"}
        require(not any(str(k).lower() in forbidden for k in value))
        technical = {"created_at", "updated_at", "ingested_at", "ingestion_timestamp"}
        return {str(k):canonical(v) for k,v in value.items() if str(k).lower() not in technical}
    if isinstance(value, (tuple, list)): return [canonical(v) for v in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True, ensure_ascii=True,
        separators=(",", ":"), allow_nan=False).encode("ascii")).hexdigest()


def signed(value, field):
    return value.model_copy(update={field:digest(value.model_dump(exclude={field}))})


def validate_day(day, genesis_date, end_date):
    require(type(day) is date and genesis_date <= day <= end_date)


def validate_benchmark_genesis(genesis):
    strict(genesis, OfzBenchmarkGenesisViewV1)
    require(genesis.status == "READY" and not genesis.blockers)
    require(genesis.benchmark_genesis_sha256 == digest(genesis.model_dump(exclude={"benchmark_genesis_sha256"})))
    require(genesis.experiment_policy_sha256 == digest(genesis.policy) and
        genesis.planned_end_date == genesis.genesis_date + timedelta(days=90) and
        finite(genesis.initial_capital_rub, positive=True) and finite(genesis.target_duration_years, positive=True))
    require(genesis.common_market_trade_date is not None and
        0 <= (genesis.genesis_date-genesis.common_market_trade_date).days <= 7)
    require(len(genesis.components) in (1,2) and len({c.representative.bond_id for c in genesis.components}) == len(genesis.components))
    require(tuple(genesis.representatives) == tuple(sorted(genesis.representatives,
        key=lambda r:(r.modified_duration_years,r.bond_id,r.snapshot_id))))
    curve = genesis.curve
    require(curve is not None and curve.status == "READY" and
        (curve.as_of_date,curve.market_source,curve.curve_trade_date) ==
        (genesis.genesis_date,"moex",genesis.common_market_trade_date) and
        type(curve.node_count) is int and curve.node_count == len(curve.nodes) >= 2 and
        len(genesis.representatives) == len(genesis.modified_duration_inputs) == curve.node_count and
        genesis.curve_build_count == 1 and genesis.modified_duration_call_count == curve.node_count and
        genesis.dirty_value_call_count == len(genesis.components))
    require(len({r.bond_id for r in genesis.representatives}) == len(genesis.representatives))
    previous = ZERO
    for node in curve.nodes:
        arrays = (node.component_bond_ids,node.component_snapshot_ids,node.component_isins,
                  node.component_secids,node.component_yields_pct)
        require(finite(node.duration_years,positive=True) and node.duration_years > previous and
            finite(node.yield_to_maturity_pct) and len(arrays[0]) > 0 and
            all(len(a) == len(arrays[0]) for a in arrays))
        previous = node.duration_years
        matches = [r for r in genesis.representatives if r.source_node_macaulay_duration_years == node.duration_years]
        require(len(matches) == 1)
        r = matches[0]; v = r.modified_duration_evidence
        require((r.bond_id,r.snapshot_id,r.isin,r.secid,r.component_yield_pct) in tuple(zip(*arrays)) and
            r.source_node_yield_pct == node.yield_to_maturity_pct and v in genesis.modified_duration_inputs and
            v.status == "READY" and v.availability.has_modified_duration is True and
            finite(v.modified_duration_years,positive=True) and r.modified_duration_years == v.modified_duration_years and
            (v.bond_id,v.market_snapshot_id,v.isin,v.secid,v.market_trade_date,v.as_of_date,v.market_source) ==
            (r.bond_id,r.snapshot_id,r.isin,r.secid,genesis.common_market_trade_date,genesis.genesis_date,"moex") and
            v.macaulay_duration_years == node.duration_years and v.yield_to_maturity_pct == r.component_yield_pct)
    require((curve.min_duration_years,curve.max_duration_years) ==
        (curve.nodes[0].duration_years,curve.nodes[-1].duration_years))
    with localcontext(DECIMAL_CONTEXT):
        total = ZERO; duration = ZERO
        for c in genesis.components:
            r = c.representative; v = c.genesis_market_evidence
            require(r in genesis.representatives and finite(c.weight, positive=True) and
                finite(c.genesis_dirty_value_rub, positive=True) and finite(r.modified_duration_years, positive=True))
            require((v.bond_id,v.market_snapshot_id,v.market_trade_date,v.as_of_date,v.market_source) ==
                (r.bond_id,r.snapshot_id,genesis.common_market_trade_date,genesis.genesis_date,"moex") and
                (v.isin,v.secid) == (r.isin,r.secid) and v.availability.has_dirty_value is True and
                v.currency_code == "RUB" and v.currency_state == v.nominal_state == "verified" and
                finite(v.nominal_value,positive=True) and v.dirty_value_currency == c.genesis_dirty_value_rub and
                v.provenance.security_master_profile_id == r.modified_duration_evidence.provenance.security_master_profile_id)
            total += c.weight; duration += c.weight*r.modified_duration_years
        require(total == ONE and duration == genesis.target_duration_years == genesis.reconstructed_duration_years and
            genesis.genesis_nav_rub == genesis.initial_capital_rub, "BENCHMARK_DECIMAL_INVARIANTS_FAILED")
    if genesis.matching_mode == "EXACT_DURATION_NODE":
        require(len(genesis.components) == 1 and genesis.components[0].weight == ONE)
    else:
        require(genesis.matching_mode == "LINEAR_DURATION_MATCH" and len(genesis.components) == 2 and
            genesis.components[0].representative.modified_duration_years < genesis.target_duration_years <
            genesis.components[1].representative.modified_duration_years)


def validate_experiment(experiment):
    strict(experiment, ShadowExperimentGenesisPlanV1)
    require(experiment.status == "READY" and not experiment.blockers and experiment.benchmark is not None)
    require(experiment.experiment_genesis_sha256 == digest(experiment.model_dump(exclude={"experiment_genesis_sha256"})))
    p = experiment.reviewed_strategy_genesis; b = experiment.benchmark; s = experiment.source_shadow_execution
    validate_benchmark_genesis(b)
    require(p.status == "EXECUTABLE" and not p.blockers and p.snapshot is not None and
        p.plan_sha256 == digest(p.model_dump(exclude={"plan_sha256"})))
    require(experiment.strategy_genesis_plan_sha256 == p.plan_sha256 and
        experiment.shadow_execution_sha256 == p.shadow_execution_sha256 == b.shadow_execution_sha256 == digest(s) and
        experiment.source_universe_sha256 == p.source_universe_sha256 and
        experiment.experiment_policy_sha256 == b.experiment_policy_sha256 == digest(experiment.policy) and
        experiment.policy == b.policy and experiment.benchmark_genesis_sha256 == b.benchmark_genesis_sha256)
    require(experiment.genesis_date == p.as_of_date == b.genesis_date == s.as_of_date and
        experiment.planned_end_date == p.planned_end_date == b.planned_end_date and
        experiment.initial_capital_rub == p.snapshot.nav_rub == b.initial_capital_rub == s.summary.capital_rub and
        experiment.target_duration_years == b.target_duration_years ==
        s.post_rounding_risk_evaluation.metrics.invested_weighted_modified_duration_years and
        experiment.common_market_trade_date == b.common_market_trade_date and
        all(v.market_trade_date == b.common_market_trade_date for v in s.positions))


class ShadowExperimentEvaluator:
    @staticmethod
    def build(*, experiment_genesis, strategy_observation, benchmark_daily):
        validate_experiment(experiment_genesis)
        strict(strategy_observation, StrategyExperimentObservationV1)
        strict(benchmark_daily, OfzBenchmarkDailyViewV1)
        e, o, b = experiment_genesis, strategy_observation, benchmark_daily
        validate_day(o.as_of_date, e.genesis_date, e.planned_end_date)
        require(b.benchmark_daily_sha256 == digest(b.model_dump(exclude={"benchmark_daily_sha256"})))
        blockers = set(o.blockers) | set(b.blockers)
        if ((o.as_of_date,o.genesis_date,o.initial_capital_rub,o.run_key_sha256,o.genesis_plan_sha256,
             o.shadow_execution_sha256,o.source_universe_sha256) !=
            (b.as_of_date,e.genesis_date,e.initial_capital_rub,e.reviewed_strategy_genesis.run_key_sha256,
             e.strategy_genesis_plan_sha256,e.shadow_execution_sha256,e.source_universe_sha256) or
            (b.genesis_date,b.planned_end_date,b.initial_capital_rub,b.benchmark_genesis_sha256,b.experiment_policy_sha256) !=
            (e.genesis_date,e.planned_end_date,e.initial_capital_rub,e.benchmark_genesis_sha256,e.experiment_policy_sha256)):
            blockers.add("COMPARISON_IDENTITY_MISMATCH")
        if o.status == "READY":
            expected_dates = tuple(e.genesis_date+timedelta(days=i) for i in range((o.as_of_date-e.genesis_date).days+1))
            if (o.snapshot is None or o.snapshot.as_of_date != o.as_of_date or
                o.accepted_dates != expected_dates or len(o.audits) != len(expected_dates) or
                any(a.status != "VERIFIED" or a.blockers or a.run_key_sha256 != o.run_key_sha256 for a in o.audits) or
                (o.audits and o.audits[0].snapshot_sha256 != e.reviewed_strategy_genesis.snapshot.snapshot_sha256) or
                (o.snapshot is not None and (not finite(o.snapshot.cumulative_return) or
                  not finite(o.snapshot.nav_rub) or o.snapshot.nav_rub < 0 or
                  o.snapshot.snapshot_sha256 != digest(o.snapshot.model_dump(exclude={"snapshot_sha256"})) or
                  not o.audits or o.audits[-1].snapshot_sha256 != o.snapshot.snapshot_sha256))):
                blockers.add("STRATEGY_HISTORY_INVALID")
        else: blockers.add("STRATEGY_OBSERVATION_UNAVAILABLE")
        if b.status != "READY" or not all(finite(v) for v in
            (b.benchmark_nav_rub,b.previous_nav_rub,b.daily_return,b.cumulative_return)) or b.benchmark_nav_rub < 0 or b.previous_nav_rub <= 0:
            blockers.add("BENCHMARK_MARKET_VALUE_UNAVAILABLE")
        complete = not blockers
        strategy_return = benchmark_return = excess = None
        reasons = []
        verdict = "IN_PROGRESS"
        if complete:
            strategy_return = o.snapshot.cumulative_return
            benchmark_return = b.cumulative_return
            with localcontext(DECIMAL_CONTEXT): excess = strategy_return-benchmark_return
        if o.as_of_date == e.planned_end_date:
            if not complete: verdict = "INDETERMINATE"
            else:
                if strategy_return <= ZERO: reasons.append("ABSOLUTE_RETURN_NONPOSITIVE")
                if excess <= ZERO: reasons.append("OFZ_EXCESS_RETURN_NONPOSITIVE")
                verdict = "FAIL" if reasons else "PASS"
        result = ShadowExperimentComparisonViewV1(status="READY" if complete else "UNAVAILABLE", verdict=verdict,
            as_of_date=o.as_of_date, genesis_date=e.genesis_date, planned_end_date=e.planned_end_date,
            initial_capital_rub=e.initial_capital_rub, experiment_genesis_sha256=e.experiment_genesis_sha256,
            strategy_return=strategy_return, benchmark_return=benchmark_return, excess_return=excess,
            reasons=tuple(sorted(reasons)), blockers=tuple(sorted(blockers)), strategy_observation=o, benchmark_daily=b)
        return signed(result, "comparison_sha256")
