"""Read-only experiment bundling and accepted Task303 history observation."""
from datetime import timedelta
import re
from app.schemas.shadow_execution import ShadowExecutionPlanView
from app.schemas.shadow_ledger import ShadowGenesisPlanV1, ShadowGenesisRequestV1
from app.schemas.shadow_experiment import (
    ShadowExperimentPolicyV1, ShadowExperimentGenesisPlanV1, StrategyExperimentObservationV1,
)
from app.services.shadow_ledger_genesis_service import validate_request
from app.services import shadow_ledger_repository as repository
from app.services.ofz_total_return_benchmark_service import OfzTotalReturnBenchmarkService, evidence_code
from app.services.shadow_experiment_evaluator import (
    strict, require, digest, signed, validate_experiment, validate_day, ShadowExperimentEvaluator,
)


def validate_reviewed_genesis(plan, source):
    strict(plan, ShadowGenesisPlanV1); strict(source, ShadowExecutionPlanView)
    require(plan.status == "EXECUTABLE" and not plan.blockers,"STRATEGY_GENESIS_NOT_EXECUTABLE")
    validate_request(ShadowGenesisRequestV1(shadow_execution=source,source_code_sha=plan.source_code_sha))
    require(plan.plan_sha256 == digest(plan.model_dump(exclude={"plan_sha256"})),"STRATEGY_GENESIS_EVIDENCE_MISMATCH")
    require(re.fullmatch(r"[0-9a-f]{40}",plan.source_code_sha) is not None)
    universe = source.source_strategy.source_investment_batch.source_batch
    universe_hash = digest({name:getattr(universe,name) for name in
        ("as_of_date","market_source","requested_bond_ids","existing_bond_ids","missing_bond_ids","candidate_bond_ids")})
    execution_hash = digest(source)
    require(plan.shadow_execution_sha256 == plan.input_state_sha256 == execution_hash and
        plan.source_universe_sha256 == universe_hash and plan.run_key_sha256 == digest(dict(
            shadow_execution_sha256=execution_hash,source_universe_sha256=universe_hash,
            source_code_sha=plan.source_code_sha,horizon_days=90)) and
        plan.current_shadow_db_state_sha256 == repository.state_hash(dict(run=None,ledger=[],snapshots=[],positions=[])),
        "STRATEGY_GENESIS_EVIDENCE_MISMATCH")
    require(plan.as_of_date == source.as_of_date and plan.planned_end_date == source.as_of_date+timedelta(days=90) and
        plan.snapshot is not None and plan.snapshot.as_of_date == source.as_of_date and
        plan.snapshot.cash_rub == source.summary.shadow_cash_rub and
        plan.snapshot.market_value_rub == source.summary.planned_shadow_invested_rub and
        plan.snapshot.nav_rub == source.summary.capital_rub and
        plan.snapshot.daily_return == plan.snapshot.cumulative_return == 0 and
        plan.snapshot.previous_snapshot_sha256 is None and plan.snapshot.input_state_sha256 == execution_hash and
        plan.snapshot.snapshot_sha256 == digest(plan.snapshot.model_dump(exclude={"snapshot_sha256"})),
        "STRATEGY_GENESIS_EVIDENCE_MISMATCH")
    positions = sorted(source.positions,key=lambda p:p.bond_id)
    require(len(plan.positions) == len(positions) and len(plan.events) == len(positions)+1,
        "STRATEGY_GENESIS_EVIDENCE_MISMATCH")
    initial = plan.events[0]
    require(initial.event_type == "INITIAL_CAPITAL" and initial.cash_delta_rub == source.summary.capital_rub and
        initial.sequence_number == 1 and initial.quantity_delta == 0 and initial.bond_id is None and
        initial.source_fingerprint_sha256 == execution_hash,"STRATEGY_GENESIS_EVIDENCE_MISMATCH")
    for n,(position,purchase,original) in enumerate(zip(plan.positions,plan.events[1:],positions),2):
        require(position.bond_id == purchase.bond_id == original.bond_id and position.position_status == "ACTIVE" and
            position.quantity == purchase.quantity_delta == original.planned_bond_quantity and
            position.dirty_value_rub_per_bond == purchase.unit_amount_rub == original.dirty_value_currency and
            position.market_value_rub == original.planned_cash_cost_rub and purchase.cash_delta_rub.copy_negate() == original.planned_cash_cost_rub and
            position.market_snapshot_id == purchase.source_market_snapshot_id == original.market_snapshot_id and
            position.market_trade_date == original.market_trade_date and position.price_basis == original.price_basis and
            position.market_age_days == (source.as_of_date-original.market_trade_date).days and position.cashflow_rub_on_date == 0 and
            position.source_security_master_profile_id == original.security_master_profile_id and
            purchase.event_type == "GENESIS_PURCHASE" and purchase.sequence_number == n and
            purchase.source_fingerprint_sha256 == digest(original),"STRATEGY_GENESIS_EVIDENCE_MISMATCH")
    require(plan.snapshot.active_position_count == len(positions) and plan.snapshot.redeemed_position_count == 0 and
        plan.snapshot.applied_cashflow_count_for_day == 0 and plan.snapshot.ledger_entry_count_to_date == len(plan.events),
        "STRATEGY_GENESIS_EVIDENCE_MISMATCH")
    for event in plan.events:
        require(event.event_date == source.as_of_date and event.source_contract_version == source.contract_version and
            event.event_key_sha256 == digest({"run_key_sha256":plan.run_key_sha256,
                **event.model_dump(exclude={"event_key_sha256","pit_ready"})}),"STRATEGY_GENESIS_EVIDENCE_MISMATCH")
    for position in plan.positions:
        require(position.position_state_sha256 == digest(position.model_dump(exclude={"position_state_sha256"})),
            "STRATEGY_GENESIS_EVIDENCE_MISMATCH")


class ShadowExperimentGenesisService:
    def __init__(self, db): self.db = db

    def build(self, *, reviewed_genesis_plan, shadow_execution, policy):
        strict(reviewed_genesis_plan,ShadowGenesisPlanV1); strict(shadow_execution,ShadowExecutionPlanView)
        strict(policy,ShadowExperimentPolicyV1)
        p,s = reviewed_genesis_plan,shadow_execution
        base = dict(policy=policy,reviewed_strategy_genesis=p,source_shadow_execution=s,genesis_date=s.as_of_date,
            planned_end_date=s.as_of_date+timedelta(days=90),initial_capital_rub=s.summary.capital_rub,
            target_duration_years=s.post_rounding_risk_evaluation.metrics.invested_weighted_modified_duration_years,
            experiment_policy_sha256=digest(policy),strategy_genesis_plan_sha256=p.plan_sha256,
            shadow_execution_sha256=p.shadow_execution_sha256,source_universe_sha256=p.source_universe_sha256)
        benchmark = None
        try:
            validate_reviewed_genesis(p,s)
            with self.db.no_autoflush:
                benchmark = OfzTotalReturnBenchmarkService(self.db).build_genesis(shadow_execution=s,policy=policy)
            if benchmark.status != "READY":
                result = ShadowExperimentGenesisPlanV1(**base,status="BLOCKED",benchmark=benchmark,blockers=benchmark.blockers)
            else:
                result = ShadowExperimentGenesisPlanV1(**base,status="READY",benchmark=benchmark,
                    benchmark_genesis_sha256=benchmark.benchmark_genesis_sha256,
                    common_market_trade_date=benchmark.common_market_trade_date)
        except ValueError as exc:
            result = ShadowExperimentGenesisPlanV1(**base,status="BLOCKED",benchmark=benchmark,
                blockers=(evidence_code(exc,"INPUT_EVIDENCE_INVALID"),))
        return signed(result,"experiment_genesis_sha256")


class ShadowExperimentComparisonService:
    def __init__(self, db): self.db = db

    def build(self, *, experiment_genesis, as_of_date):
        validate_experiment(experiment_genesis)
        e = experiment_genesis; p = e.reviewed_strategy_genesis
        validate_day(as_of_date,e.genesis_date,e.planned_end_date)
        base = dict(as_of_date=as_of_date,genesis_date=e.genesis_date,initial_capital_rub=e.initial_capital_rub,
            run_key_sha256=p.run_key_sha256,genesis_plan_sha256=p.plan_sha256,
            shadow_execution_sha256=p.shadow_execution_sha256,source_universe_sha256=p.source_universe_sha256)
        dates = (); audits = []; snapshot = None
        with self.db.no_autoflush:
            try:
                state = repository.read_state(self.db,p.run_key_sha256)
                run = state["run"]
                require(run is not None,"STRATEGY_OBSERVATION_UNAVAILABLE")
                require(repository.audit_genesis(self.db,p).status == "VERIFIED" and
                    run["genesis_date"] == e.genesis_date and run["planned_end_date"] == e.planned_end_date and
                    run["initial_capital_rub"] == e.initial_capital_rub and run["base_currency"] == "RUB" and
                    run["market_source"] == "moex" and run["horizon_days"] == 90 and run["status"] != "ABORTED",
                    "STRATEGY_HISTORY_INVALID")
                history = [s for s in state["snapshots"] if s["as_of_date"] <= as_of_date]
                dates = tuple(s["as_of_date"] for s in history)
                expected = tuple(e.genesis_date+timedelta(days=i) for i in range((as_of_date-e.genesis_date).days+1))
                require(dates == expected,"STRATEGY_HISTORY_INVALID")
                for row in history:
                    snapshot = repository.stored_snapshot(row)
                    audit = repository.audit(self.db,p.run_key_sha256,snapshot)
                    audits.append(audit)
                    require(audit.status == "VERIFIED","STRATEGY_HISTORY_INVALID")
                observation = StrategyExperimentObservationV1(**base,status="READY",snapshot=snapshot,
                    accepted_dates=dates,audits=tuple(audits))
            except (ValueError, KeyError, TypeError) as exc:
                observation = StrategyExperimentObservationV1(**base,status="UNAVAILABLE",snapshot=None,
                    accepted_dates=dates,audits=tuple(audits),blockers=(evidence_code(exc,"STRATEGY_HISTORY_INVALID"),))
            benchmark = OfzTotalReturnBenchmarkService(self.db).build_daily(genesis=e.benchmark,as_of_date=as_of_date)
            return ShadowExperimentEvaluator.build(experiment_genesis=e,strategy_observation=observation,benchmark_daily=benchmark)
