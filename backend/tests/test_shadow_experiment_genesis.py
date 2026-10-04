"""Read-only experiment binding and accepted-history integration."""
from copy import deepcopy
from datetime import timedelta
from sqlalchemy import event
import pytest
from test_shadow_ledger_genesis import environment, genesis
from test_ofz_total_return_benchmark import benchmark_env
from app.schemas.shadow_experiment import ShadowExperimentPolicyV1
from app.services.shadow_ledger_genesis_service import ShadowLedgerGenesisService
from app.services.shadow_experiment_genesis_service import ShadowExperimentGenesisService, ShadowExperimentComparisonService
from app.services.shadow_experiment_evaluator import signed, validate_experiment
from app.models.shadow_test_run import ShadowTestRun
from app.services.shadow_daily_cycle_service import ShadowDailyCycleService
from test_shadow_daily_cycle import authorization as daily_authorization


def experiment(env):
    plan=ShadowLedgerGenesisService(env.factory).plan(request=env.request)
    result=ShadowExperimentGenesisService(env.db).build(reviewed_genesis_plan=plan,
        shadow_execution=env.request.shadow_execution,policy=ShadowExperimentPolicyV1())
    assert result.status=='READY',result.blockers
    return result


def test_bundle_read_only_full_bindings_and_determinism(benchmark_env):
    e=benchmark_env; sql=[]
    event.listen(e.engine,'before_cursor_execute',lambda c,u,s,p,x,m:sql.append(s))
    before=deepcopy(e.request.model_dump()); result=experiment(e)
    validate_experiment(result)
    assert result==experiment(e)
    assert e.request.model_dump()==before
    assert sql and all(s.lstrip().upper().startswith('SELECT') for s in sql)
    assert result.strategy_genesis_plan_sha256==result.reviewed_strategy_genesis.plan_sha256
    assert result.target_duration_years==2 and result.common_market_trade_date==e.day
    assert result.capabilities.experiment_day0_started is False


@pytest.mark.parametrize('field', ['status','plan_sha256','source_universe_sha256','shadow_execution_sha256'])
def test_reviewed_plan_tampering_blocks_before_benchmark(benchmark_env,field):
    e=benchmark_env; p=ShadowLedgerGenesisService(e.factory).plan(request=e.request)
    p=p.model_copy(update={field:'BLOCKED' if field=='status' else '0'*64})
    result=ShadowExperimentGenesisService(e.db).build(reviewed_genesis_plan=p,
        shadow_execution=e.request.shadow_execution,policy=ShadowExperimentPolicyV1())
    assert result.status=='BLOCKED' and result.benchmark is None


def test_accepted_genesis_history_and_missing_date(benchmark_env):
    e=benchmark_env; bundle=experiment(e)
    plan,receipt=genesis((e.factory,e.request,e.engine))
    assert plan==bundle.reviewed_strategy_genesis
    result=ShadowExperimentComparisonService(e.db).build(experiment_genesis=bundle,as_of_date=e.day)
    assert result.status=='READY',result.blockers
    assert result.verdict=='IN_PROGRESS' and result.strategy_return==result.benchmark_return==0
    assert result.strategy_observation.audits[0].status=='VERIFIED'
    later=ShadowExperimentComparisonService(e.db).build(experiment_genesis=bundle,as_of_date=e.day+timedelta(days=1))
    assert later.status=='UNAVAILABLE' and later.verdict=='IN_PROGRESS'
    assert 'STRATEGY_HISTORY_INVALID' in later.blockers
    assert later.strategy_return is later.excess_return is None
    daily_service=ShadowDailyCycleService(e.factory)
    daily=daily_service.plan(run_key_sha256=plan.run_key_sha256,as_of_date=e.day+timedelta(days=1))
    assert daily.status=='EXECUTABLE',daily.blockers
    assert daily_service.apply(reviewed_plan=daily,authorization=daily_authorization(daily)).status=='APPLIED'
    accepted=ShadowExperimentComparisonService(e.db).build(experiment_genesis=bundle,as_of_date=daily.as_of_date)
    assert accepted.status=='READY' and len(accepted.strategy_observation.audits)==2


def test_missing_run_and_bundle_tampering(benchmark_env):
    e=benchmark_env; bundle=experiment(e)
    output=ShadowExperimentComparisonService(e.db).build(experiment_genesis=bundle,as_of_date=e.day)
    assert output.status=='UNAVAILABLE' and 'STRATEGY_OBSERVATION_UNAVAILABLE' in output.blockers
    altered=bundle.model_copy(update={'initial_capital_rub':bundle.initial_capital_rub+1})
    with pytest.raises(ValueError): validate_experiment(altered)
    with pytest.raises(ValueError): validate_experiment(signed(altered,'experiment_genesis_sha256'))


def test_accepted_run_provenance_drift_blocks_without_repair(benchmark_env):
    e=benchmark_env; bundle=experiment(e); genesis((e.factory,e.request,e.engine))
    with e.factory() as db:
        run=db.query(ShadowTestRun).one()
        run.genesis_plan_sha256='0'*64
        db.commit()
    result=ShadowExperimentComparisonService(e.db).build(experiment_genesis=bundle,as_of_date=e.day)
    assert result.status=='UNAVAILABLE' and 'STRATEGY_HISTORY_INVALID' in result.blockers
