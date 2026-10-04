"""Seven independently built capital chains, isolated SQLite and immutable evidence."""
import ast
from copy import deepcopy
from decimal import Decimal, localcontext, ROUND_DOWN
from pathlib import Path
from types import SimpleNamespace
import pytest
from sqlalchemy import event
from app.schemas.shadow_scale_experiment import (
    CAPITAL_GRID_RUB, ShadowScaleExperimentPolicyV1, ShadowScaleExperimentCaseInputV1,
    ShadowScaleExperimentGenesisPlanV1,
)
from app.schemas.shadow_ledger import ShadowGenesisRequestV1
from app.services.shadow_scale_experiment_genesis_service import ShadowScaleExperimentGenesisService
from app.services import shadow_scale_experiment_evaluator as pure
from app.services.shadow_experiment_genesis_service import ShadowExperimentGenesisService
from app.services.shadow_ledger_genesis_service import ShadowLedgerGenesisService
from app.services.shadow_execution_planner import ShadowExecutionPlanner
from test_shadow_execution_terms_loader import strategy, terms
from test_shadow_ledger_genesis import environment, unique_identities
from test_ofz_total_return_benchmark import benchmark_env

D = Decimal


@pytest.fixture(scope="session")
def prepared(tmp_path_factory):
    isolated = environment.__wrapped__(tmp_path_factory.mktemp("scale"))
    env = next(isolated)
    reference = benchmark_env.__wrapped__(env)
    db_env = next(reference)
    inputs = []
    for capital in CAPITAL_GRID_RUB:
        changes = {i: {"dirty_value_currency": D(1020), "clean_price": D("101.85"),
            "clean_quote_pct": D("101.85"), "clean_value_currency": D("1018.5"),
            "median_daily_turnover_value": D("2000000") if i == 3 else D("1000000000"),
            "modified_duration_years": D("3.2") if i == 3 else D(2)} for i in (1, 2, 3)}
        s = unique_identities(strategy(overrides=changes, capital=str(capital)))
        shadow = ShadowExecutionPlanner.build(s, terms(s))
        request = ShadowGenesisRequestV1(shadow_execution=shadow, source_code_sha="a"*40)
        plan = ShadowLedgerGenesisService(db_env.factory).plan(request=request)
        assert plan.status == "EXECUTABLE", plan.blockers
        inputs.append(ShadowScaleExperimentCaseInputV1(capital_rub=capital,
            reviewed_strategy_genesis=plan, source_shadow_execution=shadow))
    calls = []
    event.listen(db_env.engine, "before_cursor_execute", lambda c,u,s,p,x,m: calls.append(s))
    matrix = ShadowScaleExperimentGenesisService(db_env.db).build(cases=tuple(inputs), scale_policy=ShadowScaleExperimentPolicyV1())
    assert matrix.status == "READY", matrix.blockers
    assert calls and all(s.lstrip().upper().startswith("SELECT") for s in calls)
    yield SimpleNamespace(env=db_env, inputs=tuple(inputs), matrix=matrix)
    reference.close()
    isolated.close()


def cached_authority(monkeypatch, prepared, calls):
    by_capital = {c.capital_rub: c.experiment_genesis for c in prepared.matrix.cases}
    def build(self, **kwargs):
        calls.append(kwargs)
        return by_capital[kwargs["shadow_execution"].summary.capital_rub]
    monkeypatch.setattr(ShadowExperimentGenesisService, "build", build)


def test_independent_chains_metrics_permutation_and_pending_state(prepared, monkeypatch):
    p = prepared
    calls = []
    cached_authority(monkeypatch, p, calls)
    before = deepcopy(tuple(c.model_dump() for c in p.inputs))
    db = p.env.db
    from app.models.company import Company
    pending = Company(name="Caller pending", ticker="PENDING304A")
    db.add(pending)
    with db.no_autoflush:
        from sqlalchemy import select
        existing = db.scalars(select(Company).limit(1)).first()
    existing.name = "Caller dirty"
    state = (set(db.new), set(db.dirty), set(db.deleted))
    with localcontext() as ctx:
        ctx.prec = 5; ctx.rounding = ROUND_DOWN
        result = ShadowScaleExperimentGenesisService(db).build(cases=tuple(reversed(p.inputs)), scale_policy=p.matrix.scale_policy)
        assert ctx.prec == 5 and ctx.rounding == ROUND_DOWN
    assert result == p.matrix
    assert len(calls) == 7 and [c["shadow_execution"].summary.capital_rub for c in calls] == list(CAPITAL_GRID_RUB)
    assert state == (set(db.new), set(db.dirty), set(db.deleted))
    db.expunge(pending); db.expire(existing)
    assert before == tuple(c.model_dump() for c in p.inputs)
    assert len({c.investment_batch_sha256 for c in result.cases}) == 1
    assert len({c.case_sha256 for c in result.cases}) == 7
    assert len({c.experiment_genesis.benchmark_genesis_sha256 for c in result.cases}) == 7
    assert len({c.target_duration_years for c in result.cases}) > 1
    assert len({tuple(p.bond_id for p in c.source_shadow_execution.positions) for c in result.cases}) > 1
    for c in result.cases:
        s = c.source_shadow_execution
        assert c.shadow_planned_invested_rub == s.summary.planned_shadow_invested_rub
        assert c.shadow_residual_cash_rub == s.summary.shadow_cash_rub
        assert c.target_duration_years == s.post_rounding_risk_evaluation.metrics.invested_weighted_modified_duration_years
        assert c.experiment_genesis.benchmark.initial_capital_rub == c.capital_rub
    assert ShadowScaleExperimentGenesisPlanV1.model_validate_json(result.model_dump_json()) == result
    with pytest.raises(ValueError): result.status = "BLOCKED"
    with pytest.raises(ValueError): ShadowScaleExperimentPolicyV1(unexpected=True)


@pytest.mark.parametrize("bad", [None, "abc", {}, set(), iter(()), (), (None,)*7])
def test_bad_sequences_before_calls(bad, prepared, monkeypatch):
    calls = []; cached_authority(monkeypatch, prepared, calls)
    with pytest.raises(ValueError):
        ShadowScaleExperimentGenesisService(prepared.env.db).build(cases=bad, scale_policy=prepared.matrix.scale_policy)
    assert calls == []


@pytest.mark.parametrize("value", [50000, True, "50000", 50000.0, D("NaN"), D("1")])
def test_capital_original_types_and_binding(value, prepared, monkeypatch):
    calls = []; cached_authority(monkeypatch, prepared, calls)
    bad = prepared.inputs[0].model_copy(update={"capital_rub":value})
    with pytest.raises(ValueError):
        ShadowScaleExperimentGenesisService(prepared.env.db).build(cases=(bad,*prepared.inputs[1:]), scale_policy=prepared.matrix.scale_policy)
    assert not calls


def test_fixed_policy_duplicate_missing_and_capital_substitution(prepared, monkeypatch):
    policy = prepared.matrix.scale_policy
    assert policy.capital_grid_rub == CAPITAL_GRID_RUB
    assert type(policy).model_validate_json(policy.model_dump_json()) == policy
    for grid in (CAPITAL_GRID_RUB[::-1], CAPITAL_GRID_RUB[:-1], (D(1),*CAPITAL_GRID_RUB[1:]), tuple(int(v) for v in CAPITAL_GRID_RUB)):
        with pytest.raises(ValueError): ShadowScaleExperimentPolicyV1(capital_grid_rub=grid)
    calls=[]; cached_authority(monkeypatch, prepared, calls)
    for inputs in ((prepared.inputs[0],*prepared.inputs[:-1]),
                   (prepared.inputs[0].model_copy(update={"capital_rub":D(100000)}),*prepared.inputs[1:])):
        with pytest.raises(ValueError): ShadowScaleExperimentGenesisService(prepared.env.db).build(cases=inputs,scale_policy=policy)
    assert not calls


@pytest.mark.parametrize("field", ["genesis_date", "planned_end_date", "common_market_trade_date",
    "market_source", "source_code_sha", "source_universe_sha256", "investment_batch_sha256", "experiment_policy_sha256"])
def test_cross_case_fail_closed_nullable_metadata(field, prepared):
    # Assembly operates on finalized evidence; public validation still rejects tampered READY evidence.
    cases = list(prepared.matrix.cases); c=cases[0]
    if field == "source_code_sha":
        c=c.model_copy(update={"reviewed_strategy_genesis":c.reviewed_strategy_genesis.model_copy(update={field:"b"*40})})
    elif field == "market_source":
        c=c.model_copy(update={"source_shadow_execution":c.source_shadow_execution.model_copy(update={field:"other"})})
    elif field == "investment_batch_sha256": c=c.model_copy(update={field:"0"*64})
    else:
        old=getattr(c.experiment_genesis,field)
        from datetime import timedelta
        c=c.model_copy(update={"experiment_genesis":c.experiment_genesis.model_copy(update={field:old+timedelta(days=1) if hasattr(old,"year") else "0"*64})})
    cases[0]=c
    result=pure.assemble_genesis(tuple(cases),prepared.matrix.scale_policy)
    assert result.status=="BLOCKED" and getattr(result,field) is None
    assert result.ready_case_count==7


def test_blocked_case_terms_drift_and_static_boundary(prepared, monkeypatch):
    inputs=list(prepared.inputs)
    p=inputs[0].reviewed_strategy_genesis.model_copy(update={"status":"BLOCKED","blockers":("GENESIS_NOT_READY",),"snapshot":None})
    inputs[0]=inputs[0].model_copy(update={"reviewed_strategy_genesis":p})
    authority=ShadowExperimentGenesisService.build
    cached={c.capital_rub:c.experiment_genesis for c in prepared.matrix.cases}
    def build(self,**kw):
        return authority(self,**kw) if kw["shadow_execution"].summary.capital_rub==D(50000) else cached[kw["shadow_execution"].summary.capital_rub]
    monkeypatch.setattr(ShadowExperimentGenesisService,"build",build)
    result=ShadowScaleExperimentGenesisService(prepared.env.db).build(cases=tuple(inputs),scale_policy=prepared.matrix.scale_policy)
    assert result.status=="BLOCKED" and result.blocked_case_count==1 and len(result.cases)==7
    cases=list(prepared.matrix.cases); c=cases[0]; pos=c.source_shadow_execution.positions[0]
    pos=pos.model_copy(update={"execution_terms":pos.execution_terms.model_copy(update={"lot_size":2})})
    cases[0]=c.model_copy(update={"source_shadow_execution":c.source_shadow_execution.model_copy(update={"positions":(pos,*c.source_shadow_execution.positions[1:])})})
    assert "CROSS_CASE_EXECUTION_TERMS_MISMATCH" in pure.assemble_genesis(tuple(cases),prepared.matrix.scale_policy).blockers
    for name in ("shadow_scale_experiment_genesis_service.py","shadow_scale_experiment_evaluator.py"):
        tree=ast.parse((Path(__file__).parents[1]/"app/services"/name).read_text())
        imports=[n.module or "" for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        assert not any(any(x in i for x in ("sqlalchemy","models","http","os","pathlib","random")) for i in imports)
        calls={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
        assert not calls & {"commit","flush","apply","sync","execute","now","utcnow","open"}
