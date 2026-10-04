"""Frozen observations and terminal verdicts; no loaders in the evaluator."""
import ast
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, localcontext, ROUND_DOWN
from pathlib import Path
import pytest
from test_shadow_ledger_genesis import environment
from test_ofz_total_return_benchmark import benchmark_env
from test_shadow_experiment_genesis import experiment
from app.schemas.shadow_ledger import ShadowAudit
from app.schemas.shadow_experiment import StrategyExperimentObservationV1, OfzBenchmarkDailyViewV1
from app.services import shadow_experiment_evaluator as module

D=Decimal


def observations(e,days,absolute,benchmark):
    p=e.reviewed_strategy_genesis; day=e.genesis_date+timedelta(days=days)
    snapshot=p.snapshot.model_copy(update={'as_of_date':day,'cumulative_return':absolute})
    snapshot=module.signed(snapshot,'snapshot_sha256')
    audits=tuple(ShadowAudit(status='VERIFIED',run_key_sha256=p.run_key_sha256,
        snapshot_sha256=p.snapshot.snapshot_sha256 if i==0 else snapshot.snapshot_sha256,
        ledger_entry_count=4,position_count=3) for i in range(days+1))
    o=StrategyExperimentObservationV1(status='READY',as_of_date=day,genesis_date=e.genesis_date,
        initial_capital_rub=e.initial_capital_rub,run_key_sha256=p.run_key_sha256,genesis_plan_sha256=p.plan_sha256,
        shadow_execution_sha256=e.shadow_execution_sha256,source_universe_sha256=e.source_universe_sha256,
        snapshot=snapshot,accepted_dates=tuple(e.genesis_date+timedelta(days=i) for i in range(days+1)),audits=audits)
    b=OfzBenchmarkDailyViewV1(status='READY',as_of_date=day,genesis_date=e.genesis_date,planned_end_date=e.planned_end_date,
        initial_capital_rub=e.initial_capital_rub,benchmark_genesis_sha256=e.benchmark_genesis_sha256,
        experiment_policy_sha256=e.experiment_policy_sha256,benchmark_nav_rub=e.initial_capital_rub*(1+benchmark),
        previous_nav_rub=e.initial_capital_rub,daily_return=benchmark,cumulative_return=benchmark)
    return o,module.signed(b,'benchmark_daily_sha256')


@pytest.mark.parametrize('days,absolute,reference,verdict',[(0,'0','0','IN_PROGRESS'),(89,'.1','.02','IN_PROGRESS'),
    (90,'.03','.02','PASS'),(90,'-.01','-.02','FAIL'),(90,'.02','.02','FAIL'),(90,'0','-.1','FAIL'),(90,'.01','.02','FAIL')])
def test_terminal_verdict_only_on_day90(benchmark_env,days,absolute,reference,verdict):
    e=experiment(benchmark_env); o,b=observations(e,days,D(absolute),D(reference))
    before=deepcopy((e.model_dump(),o.model_dump(),b.model_dump()))
    with localcontext() as ctx:
        ctx.prec=4;ctx.rounding=ROUND_DOWN
        result=module.ShadowExperimentEvaluator.build(experiment_genesis=e,strategy_observation=o,benchmark_daily=b)
        assert ctx.prec==4 and ctx.rounding==ROUND_DOWN
    assert result.status=='READY' and result.verdict==verdict
    assert result.excess_return==D(absolute)-D(reference)
    assert before==(e.model_dump(),o.model_dump(),b.model_dump())
    assert type(result).model_validate_json(result.model_dump_json())==result


@pytest.mark.parametrize('change',['missing','gap','audit','identity','benchmark'])
def test_incomplete_terminal_evidence_is_indeterminate(benchmark_env,change):
    e=experiment(benchmark_env); o,b=observations(e,90,D('.04'),D('.02'))
    if change=='missing': o=o.model_copy(update={'status':'UNAVAILABLE','snapshot':None})
    elif change=='gap': o=o.model_copy(update={'accepted_dates':o.accepted_dates[:-1]})
    elif change=='audit': o=o.model_copy(update={'audits':(o.audits[0].model_copy(update={'status':'FAILED'}),*o.audits[1:])})
    elif change=='identity': o=o.model_copy(update={'run_key_sha256':'0'*64})
    else: b=module.signed(b.model_copy(update={'status':'UNAVAILABLE','benchmark_nav_rub':None}),'benchmark_daily_sha256')
    result=module.ShadowExperimentEvaluator.build(experiment_genesis=e,strategy_observation=o,benchmark_daily=b)
    assert result.status=='UNAVAILABLE' and result.verdict=='INDETERMINATE'
    assert result.strategy_return is result.benchmark_return is result.excess_return is None
    assert result.blockers==tuple(sorted(set(result.blockers)))


def test_hash_date_type_and_static_safety(benchmark_env):
    e=experiment(benchmark_env);o,b=observations(e,90,D('.04'),D('.02'))
    for bad in (None,{},e.model_copy(update={'pit_ready':True})):
        with pytest.raises(ValueError): module.ShadowExperimentEvaluator.build(experiment_genesis=bad,strategy_observation=o,benchmark_daily=b)
    with pytest.raises(ValueError): module.ShadowExperimentEvaluator.build(experiment_genesis=e,strategy_observation=o,
        benchmark_daily=b.model_copy(update={'benchmark_daily_sha256':'0'*64}))
    with pytest.raises(ValueError): module.ShadowExperimentEvaluator.build(experiment_genesis=e,
        strategy_observation=o.model_copy(update={'as_of_date':e.planned_end_date+timedelta(days=1)}),benchmark_daily=b)
    tree=ast.parse(Path(module.__file__).read_text())
    imports=[n.module or '' for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    assert all(not any(x in name for x in ('models','services','sqlalchemy','http','os','pathlib')) for name in imports)
    calls={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
    assert not calls & {'commit','flush','apply','sync','open','now','utcnow'}
