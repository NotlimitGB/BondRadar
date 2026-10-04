"""Pure scale assembly, exact evidence binding and Task304 verdict delegation."""
from collections.abc import Sequence
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from app.schemas.shadow_scale_experiment import (
    CAPITAL_GRID_RUB, ShadowScaleExperimentPolicyV1, ShadowScaleExperimentCaseInputV1,
    ShadowScaleExperimentGenesisCaseV1, ShadowScaleExperimentGenesisPlanV1,
    ShadowScaleExperimentComparisonRowV1, ShadowScaleExperimentComparisonViewV1,
)
from app.schemas.shadow_experiment import ShadowExperimentGenesisPlanV1, ShadowExperimentComparisonViewV1
from app.services.shadow_experiment_evaluator import (
    strict, require, finite, digest, signed, validate_experiment, ShadowExperimentEvaluator,
)
from app.services.shadow_experiment_genesis_service import validate_reviewed_genesis

CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)


def sequence(values):
    require(isinstance(values, Sequence) and not isinstance(values, (str, bytes, bytearray, memoryview)))
    result = tuple(values)
    require(len(result) == len(CAPITAL_GRID_RUB), "CAPITAL_CASE_COUNT_INVALID")
    return result


def validate_inputs(cases, policy):
    strict(policy, ShadowScaleExperimentPolicyV1)
    values = sequence(cases)
    by_capital = {}
    for case in values:
        strict(case, ShadowScaleExperimentCaseInputV1)
        require(finite(case.capital_rub, positive=True) and case.capital_rub in CAPITAL_GRID_RUB)
        require(case.capital_rub not in by_capital, "DUPLICATE_CAPITAL")
        s = case.source_shadow_execution
        require(case.capital_rub == s.summary.capital_rub == s.source_strategy.request.capital_rub ==
            s.source_strategy.summary.capital_rub == s.post_rounding_risk_evaluation.metrics.capital_rub,
            "CASE_CAPITAL_MISMATCH")
        p = case.reviewed_strategy_genesis
        if p.snapshot is not None:
            require(p.snapshot.nav_rub == case.capital_rub, "CASE_CAPITAL_MISMATCH")
        require(s.market_source == "moex")
        by_capital[case.capital_rub] = case
    return tuple(by_capital[c] for c in CAPITAL_GRID_RUB)


def make_case(case, experiment):
    strict(experiment, ShadowExperimentGenesisPlanV1)
    s = case.source_shadow_execution
    p = case.reviewed_strategy_genesis
    require(experiment.initial_capital_rub == case.capital_rub and
        experiment.source_shadow_execution.model_dump() == s.model_dump() and
        experiment.reviewed_strategy_genesis.model_dump() == p.model_dump() and
        experiment.experiment_genesis_sha256 == digest(experiment.model_dump(exclude={"experiment_genesis_sha256"})),
        "EXPERIMENT_CASE_BINDING_INVALID")
    if experiment.status == "READY":
        validate_reviewed_genesis(p, s)
        validate_experiment(experiment)
    summary = s.summary
    strategy = s.source_strategy.summary
    risk = s.post_rounding_risk_evaluation.metrics
    benchmark = experiment.benchmark
    result = ShadowScaleExperimentGenesisCaseV1(
        **case.model_dump(exclude={"reviewed_strategy_genesis", "source_shadow_execution"}),
        reviewed_strategy_genesis=p, source_shadow_execution=s,
        status=experiment.status, experiment_genesis=experiment, blockers=experiment.blockers,
        strategy_selected_count=strategy.selected_count,
        strategy_target_invested_weight=strategy.invested_weight,
        strategy_target_cash_weight=strategy.cash_weight,
        shadow_planned_position_count=summary.planned_position_count,
        shadow_planned_invested_rub=summary.planned_shadow_invested_rub,
        shadow_residual_cash_rub=summary.shadow_cash_rub,
        shadow_realized_invested_weight=summary.realized_invested_weight,
        shadow_realized_cash_weight=summary.realized_cash_weight,
        shadow_execution_tracking_gap_rub=summary.execution_tracking_gap_rub,
        target_duration_years=risk.invested_weighted_modified_duration_years,
        post_rounding_risk_status=summary.post_rounding_risk_status,
        max_position_weight_observed=risk.max_position_weight_observed,
        max_issuer_weight_observed=risk.max_issuer_weight_observed,
        portfolio_dv01_per_100k_rub=risk.portfolio_dv01_per_100k_rub,
        benchmark_matching_mode=benchmark.matching_mode if benchmark else None,
        benchmark_component_bond_ids=tuple(c.representative.bond_id for c in benchmark.components) if benchmark else (),
        investment_batch_sha256=digest(s.source_strategy.source_investment_batch))
    return signed(result, "case_sha256")


def assemble_genesis(cases, policy):
    """Preserve case failures; expose common metadata only when unanimously proved."""
    blockers = set()
    common = {}
    sources = {
        "genesis_date": lambda c: c.experiment_genesis.genesis_date,
        "planned_end_date": lambda c: c.experiment_genesis.planned_end_date,
        "common_market_trade_date": lambda c: c.experiment_genesis.common_market_trade_date,
        "market_source": lambda c: c.source_shadow_execution.market_source,
        "source_code_sha": lambda c: c.reviewed_strategy_genesis.source_code_sha,
        "source_universe_sha256": lambda c: c.experiment_genesis.source_universe_sha256,
        "investment_batch_sha256": lambda c: c.investment_batch_sha256,
        "experiment_policy_sha256": lambda c: c.experiment_genesis.experiment_policy_sha256,
    }
    for field, getter in sources.items():
        values = tuple(getter(c) for c in cases)
        common[field] = values[0] if all(v == values[0] and v is not None and v != "" for v in values) else None
        if common[field] is None:
            blockers.add("CROSS_CASE_" + field.upper() + "_MISMATCH")
    terms = {}
    for case in cases:
        e = case.experiment_genesis
        if e.policy != policy.experiment_policy or e.experiment_policy_sha256 != digest(policy.experiment_policy):
            blockers.add("CROSS_CASE_EXPERIMENT_POLICY_MISMATCH")
        if case.status != "READY":
            blockers.add("CAPITAL_CASE_BLOCKED")
        for position in case.source_shadow_execution.positions:
            value = digest(position.execution_terms)
            if position.bond_id in terms and terms[position.bond_id] != value:
                blockers.add("CROSS_CASE_EXECUTION_TERMS_MISMATCH")
            terms[position.bond_id] = value
    ready = sum(c.status == "READY" for c in cases)
    result = ShadowScaleExperimentGenesisPlanV1(
        status="BLOCKED" if blockers else "READY", scale_policy=policy,
        scale_policy_sha256=digest(policy), capital_grid_rub=CAPITAL_GRID_RUB,
        **common, cases=cases, ready_case_count=ready, blocked_case_count=len(cases)-ready,
        blockers=tuple(sorted(blockers)))
    return signed(result, "scale_genesis_sha256")


def validate_scale_genesis(matrix):
    strict(matrix, ShadowScaleExperimentGenesisPlanV1)
    inputs = tuple(ShadowScaleExperimentCaseInputV1(capital_rub=c.capital_rub,
        reviewed_strategy_genesis=c.reviewed_strategy_genesis,
        source_shadow_execution=c.source_shadow_execution) for c in matrix.cases)
    ordered = validate_inputs(inputs, matrix.scale_policy)
    require(tuple(c.capital_rub for c in matrix.cases) == CAPITAL_GRID_RUB)
    rebuilt = tuple(make_case(c, original.experiment_genesis) for c, original in zip(ordered, matrix.cases))
    require(tuple(c.model_dump() for c in rebuilt) == tuple(c.model_dump() for c in matrix.cases), "GENESIS_CASE_TAMPERED")
    expected = assemble_genesis(rebuilt, matrix.scale_policy)
    require(expected.model_dump() == matrix.model_dump(), "SCALE_GENESIS_TAMPERED")
    require(matrix.status == "READY", "SCALE_GENESIS_NOT_READY")


class ShadowScaleExperimentEvaluator:
    @staticmethod
    def build(*, scale_genesis, comparisons):
        validate_scale_genesis(scale_genesis)
        supplied = sequence(comparisons)
        by_capital = {}
        for comparison in supplied:
            strict(comparison, ShadowExperimentComparisonViewV1)
            capital = comparison.initial_capital_rub
            require(finite(capital, positive=True) and capital in CAPITAL_GRID_RUB and capital not in by_capital)
            by_capital[capital] = comparison
        days = {c.as_of_date for c in supplied}
        require(len(days) == 1, "COMPARISON_DATE_MISMATCH")
        rows = []
        for case in scale_genesis.cases:
            comparison = by_capital[case.capital_rub]
            e = case.experiment_genesis
            o = comparison.strategy_observation
            b = comparison.benchmark_daily
            require((comparison.experiment_genesis_sha256, comparison.genesis_date, comparison.planned_end_date) ==
                (e.experiment_genesis_sha256, e.genesis_date, e.planned_end_date), "COMPARISON_CASE_MISMATCH")
            require((o.genesis_date, o.initial_capital_rub, o.run_key_sha256, o.genesis_plan_sha256,
                o.shadow_execution_sha256, o.source_universe_sha256) ==
                (e.genesis_date, e.initial_capital_rub, e.reviewed_strategy_genesis.run_key_sha256,
                 e.strategy_genesis_plan_sha256, e.shadow_execution_sha256, e.source_universe_sha256),
                "COMPARISON_CASE_MISMATCH")
            require((b.as_of_date, b.genesis_date, b.planned_end_date, b.initial_capital_rub,
                b.benchmark_genesis_sha256, b.experiment_policy_sha256) ==
                (o.as_of_date, e.genesis_date, e.planned_end_date, e.initial_capital_rub,
                 e.benchmark_genesis_sha256, e.experiment_policy_sha256), "COMPARISON_CASE_MISMATCH")
            replay = ShadowExperimentEvaluator.build(experiment_genesis=case.experiment_genesis,
                strategy_observation=comparison.strategy_observation, benchmark_daily=comparison.benchmark_daily)
            require(replay.model_dump() == comparison.model_dump(), "COMPARISON_EVIDENCE_MISMATCH")
            with localcontext(CONTEXT):
                profits = tuple(case.capital_rub*v if v is not None else None for v in
                    (comparison.strategy_return, comparison.benchmark_return, comparison.excess_return))
            row = ShadowScaleExperimentComparisonRowV1(capital_rub=case.capital_rub,
                case_sha256=case.case_sha256, comparison=comparison,
                experiment_genesis_sha256=case.experiment_genesis.experiment_genesis_sha256,
                status=comparison.status, verdict=comparison.verdict,
                strategy_return=comparison.strategy_return, benchmark_return=comparison.benchmark_return,
                excess_return=comparison.excess_return, strategy_profit_rub=profits[0],
                benchmark_profit_rub=profits[1], excess_profit_rub=profits[2], blockers=comparison.blockers,
                initial_realized_invested_weight=case.shadow_realized_invested_weight,
                initial_cash_weight=case.shadow_realized_cash_weight,
                initial_selected_count=case.strategy_selected_count,
                initial_target_duration_years=case.target_duration_years)
            rows.append(signed(row, "row_sha256"))
        ready = sum(r.status == "READY" for r in rows)
        complete = ready == len(CAPITAL_GRID_RUB)
        day = supplied[0].as_of_date
        def highest(field):
            if not complete: return None
            best = max(getattr(r, field) for r in rows)
            return tuple(r.capital_rub for r in rows if getattr(r, field) == best)
        result = ShadowScaleExperimentComparisonViewV1(
            status=("COMPLETE" if day == scale_genesis.planned_end_date else "IN_PROGRESS") if complete else "INDETERMINATE",
            as_of_date=day, scale_genesis=scale_genesis, scale_genesis_sha256=scale_genesis.scale_genesis_sha256,
            rows=tuple(rows), ready_case_count=ready, unavailable_case_count=len(rows)-ready,
            highest_strategy_return_capitals_rub=highest("strategy_return"),
            highest_excess_return_capitals_rub=highest("excess_return"),
            blockers=() if complete else ("CAPITAL_COMPARISON_UNAVAILABLE",))
        return signed(result, "scale_comparison_sha256")
