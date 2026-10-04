"""Hermetic Task304 reference-index tests on isolated SQLite."""
import ast
from copy import deepcopy
from datetime import timedelta, datetime
from decimal import Decimal, localcontext, ROUND_UP
from pathlib import Path
from types import SimpleNamespace
import pytest
from sqlalchemy import event
from pydantic import ValidationError
from test_shadow_ledger_genesis import environment, shadow_source
from app.models.bond import Bond
from app.models.company import Company
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_security_master_profile import BondSecurityMasterProfile
from app.models.bond_cashflow_event import BondCashflowEvent
from app.schemas.shadow_experiment import ShadowExperimentPolicyV1, BenchmarkCashflowV1
from app.services import ofz_total_return_benchmark_service as module
from app.services.shadow_experiment_evaluator import digest, validate_benchmark_genesis

D = Decimal


@pytest.fixture
def benchmark_env(environment):
    factory, request, engine = environment
    day = request.shadow_execution.as_of_date
    with factory() as db:
        company = db.query(Company).first()
        for bond_id, duration in ((10,1),(20,3)):
            db.add(Bond(id=bond_id, company_id=company.id, name="ОФЗ-ПД", isin=f"RU304{bond_id}",
                        secid=f"SU304{bond_id}", maturity_date=day+timedelta(days=1000)))
        db.flush()
        for bond_id, duration in ((10,1),(20,3)):
            db.add(BondSecurityMasterProfile(bond_id=bond_id, currency_state="verified",currency_code="RUB",
                nominal_state="verified",nominal_value=D(1000),coupon_structure="fixed",amortization_structure="bullet",
                perpetual_structure="dated",maturity_state="verified",maturity_date=day+timedelta(days=1000),
                coupon_frequency_state="verified",coupon_frequency_per_year=2))
            db.add(BondMarketSnapshot(bond_id=bond_id,trade_date=day,source="moex",clean_price=D(100),nkd=D(0),
                duration_years=D(duration),yield_to_maturity=D(0),raw_payload={"moex":{"DURATION":str(duration*365)}}))
        db.commit()
    db=factory()
    service=module.OfzTotalReturnBenchmarkService(db)
    yield SimpleNamespace(factory=factory,request=request,engine=engine,db=db,service=service,day=day)
    db.rollback(); db.close()


def build(env):
    return env.service.build_genesis(shadow_execution=env.request.shadow_execution,policy=ShadowExperimentPolicyV1())


def test_genesis_authority_calls_and_day_zero(benchmark_env,monkeypatch):
    e=benchmark_env; counts={"curve":0,"md":0,"dirty":0}
    for cls, method, key in ((module.OfzReferenceCurveService,"build_curve","curve"),
        (module.BondModifiedDurationService,"build_for_bond","md"),(module.BondDv01Service,"build_for_bond","dirty")):
        original=getattr(cls,method)
        def wrapped(*args,_original=original,_key=key,**kwargs):
            counts[_key]+=1; return _original(*args,**kwargs)
        monkeypatch.setattr(cls,method,wrapped)
    result=build(e)
    assert result.status=="READY", result.blockers
    # Task273 itself loads Task272: distinguish direct benchmark calls.
    assert counts=={"curve":1,"md":4,"dirty":2}
    assert result.modified_duration_call_count==2
    assert [c.weight for c in result.components]==[D('.5'),D('.5')]
    assert [r.source_node_macaulay_duration_years for r in result.representatives]==[D(1),D(3)]
    assert result.target_duration_years==result.reconstructed_duration_years==2
    validate_benchmark_genesis(result)
    zero=e.service.build_daily(genesis=result,as_of_date=e.day)
    assert zero.status=="READY" and zero.benchmark_nav_rub==result.initial_capital_rub
    assert zero.daily_return==zero.cumulative_return==0
    assert zero.cashflow_query_count==zero.dirty_value_call_count==0
    assert all(p.total_return_factor==1 for p in zero.components)


@pytest.mark.parametrize("change,blocker",[("status","BENCHMARK_MODIFIED_DURATION_UNAVAILABLE"),
    ("snapshot","BENCHMARK_MODIFIED_DURATION_EVIDENCE_MISMATCH"),("curve","BENCHMARK_CURVE_EVIDENCE_INVALID"),
    ("dirty","BENCHMARK_MARKET_VALUE_UNAVAILABLE"),("outside","TARGET_DURATION_OUTSIDE_OFZ_CURVE")])
def test_genesis_fail_closed(benchmark_env,monkeypatch,change,blocker):
    e=benchmark_env
    if change in ('status','snapshot','outside'):
        original=module.BondModifiedDurationService.build_for_bond
        def modified(*args,**kwargs):
            value=original(*args,**kwargs)
            if change=='status': return value.model_copy(update={'status':'MARKET_DATA_STALE'})
            if change=='snapshot': return value.model_copy(update={'market_snapshot_id':999})
            return value.model_copy(update={'modified_duration_years':D(5)})
        monkeypatch.setattr(module.BondModifiedDurationService,'build_for_bond',modified)
    elif change=='curve':
        original=module.OfzReferenceCurveService.build_curve
        monkeypatch.setattr(module.OfzReferenceCurveService,'build_curve',lambda *a,**k:original(*a,**k).model_copy(update={'node_count':99}))
    else:
        original=module.BondDv01Service.build_for_bond
        monkeypatch.setattr(module.BondDv01Service,'build_for_bond',lambda *a,**k:original(*a,**k).model_copy(update={'market_snapshot_id':999}))
    result=build(e)
    assert result.status=='BLOCKED' and blocker in result.blockers
    if change in ('status','snapshot','outside'): assert result.modified_duration_call_count==2 and result.dirty_value_call_count==0


def test_matching_modified_order_exact_ties_and_decimal_invariants(benchmark_env):
    reps=build(benchmark_env).representatives
    low=reps[0].model_copy(update={'modified_duration_years':D(3)})
    high=reps[1].model_copy(update={'modified_duration_years':D(1)})
    mode,chosen=module.duration_weights((low,high),D(2))
    assert [r.bond_id for r,w in chosen]==[20,10]
    assert module.duration_weights((low,high),D(1))[0]=='EXACT_DURATION_NODE'
    tied=high.model_copy(update={'bond_id':5})
    assert module.duration_weights((high,tied,low),D(1))[1][0][0].bond_id==5
    # No complement correction or tolerance for a repeating Decimal bracket.
    with pytest.raises(ValueError,match='DECIMAL_INVARIANTS'):
        module.duration_weights((reps[0].model_copy(update={'modified_duration_years':D(1)}),
            reps[1].model_copy(update={'modified_duration_years':D(12)})),D(4))


@pytest.mark.parametrize('kind,amount,expected',[('coupon',D(40),'READY'),('amortization',D(100),'READY'),
    ('redemption',D(1000),'READY'),('offer_redemption',None,'READY'),('other',D(10),'UNAVAILABLE'),
    ('coupon',None,'UNAVAILABLE')])
def test_cashflows_current_previous_and_redemption(benchmark_env,kind,amount,expected):
    e=benchmark_env; g=build(e)
    with e.factory() as db:
        db.add(BondCashflowEvent(bond_id=10,event_date=e.day+timedelta(days=1),event_type=kind,amount=amount,currency='RUB',source='moex'))
        db.commit()
    result=e.service.build_daily(genesis=g,as_of_date=e.day+timedelta(days=1))
    assert result.status==expected, result.blockers
    assert result.cashflow_query_count==1
    if expected=='UNAVAILABLE': assert result.benchmark_nav_rub is result.daily_return is None
    else:
        assert result.previous_nav_rub==g.initial_capital_rub
        if kind in ('coupon','amortization'):
            assert result.components[0].cash_per_original_bond_rub==amount
            assert result.cumulative_return==amount/D(2000)
        if kind=='redemption':
            assert result.components[0].status=='REDEEMED' and result.dirty_value_call_count==1
            assert result.components[0].market_evidence is None
        if kind=='offer_redemption': assert result.diagnostics==('OFFER_REDEMPTION_NOT_AUTO_EXERCISED',)


def test_duplicate_and_after_redemption_blocks_without_substitution(benchmark_env):
    e=benchmark_env; g=build(e); day=e.day+timedelta(days=1)
    f=BenchmarkCashflowV1(source_event_id=1,bond_id=10,event_date=day,event_type='redemption',source='moex',currency='RUB',amount=D(1000))
    with pytest.raises(ValueError,match='DUPLICATE'): e.service._value(g,day,(f,f),[],set())
    later=f.model_copy(update={'source_event_id':2,'event_date':day+timedelta(days=1),'event_type':'coupon'})
    with pytest.raises(ValueError,match='REDEMPTION_CONFLICT'): e.service._value(g,later.event_date,(f,later),[],set())


def test_select_only_pending_state_context_serialization_and_bounds(benchmark_env):
    e=benchmark_env; g=build(e); before=deepcopy(g.model_dump()); statements=[]
    event.listen(e.engine,'before_cursor_execute',lambda c,u,sql,p,x,m:statements.append(sql))
    pending=Company(name='Pending',ticker='PENDING304'); e.db.add(pending)
    with localcontext() as ctx:
        ctx.prec=5; ctx.rounding=ROUND_UP
        result=e.service.build_daily(genesis=g,as_of_date=e.day+timedelta(days=1))
        assert ctx.prec==5 and ctx.rounding==ROUND_UP
    assert result.status=='READY' and pending in e.db.new
    assert statements and all(s.lstrip().upper().startswith('SELECT') for s in statements)
    assert g.model_dump()==before
    assert type(result).model_validate_json(result.model_dump_json())==result
    with pytest.raises(ValidationError): result.status='UNAVAILABLE'
    with pytest.raises(ValidationError): ShadowExperimentPolicyV1(horizon_calendar_days=91)
    for day in (e.day-timedelta(days=1),e.day+timedelta(days=91),datetime(2026,9,19)):
        with pytest.raises(ValueError): e.service.build_daily(genesis=g,as_of_date=day)
    assert digest(g)==digest(deepcopy(g))


def test_static_read_only_boundary():
    tree=ast.parse(Path(module.__file__).read_text())
    calls={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
    assert not calls & {'flush','commit','delete','apply','sync','post'}
    assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='add' and
        isinstance(n.func.value,ast.Attribute) and n.func.value.attr=='db' for n in ast.walk(tree))
    assert 'build_for_bond' in calls and 'build_curve' in calls


def test_representative_yield_ties_alignment_and_weekend(benchmark_env):
    e=benchmark_env; g=build(e); curve=g.curve; node=curve.nodes[0]
    median=node.model_copy(update={'aggregation_method':'MEDIAN','yield_to_maturity_pct':D(10),
        'component_bond_ids':[31,30,29],'component_snapshot_ids':[301,300,299],
        'component_secids':['SU31','SU30','SU29'],'component_isins':['RU31','RU30','RU29'],
        'component_yields_pct':[D(9),D(11),D(8)]})
    altered=curve.model_copy(update={'nodes':[median,curve.nodes[1]]})
    assert module.curve_representatives(altered,e.day)[0][1][0]==30
    weekend=curve.model_copy(update={'curve_trade_date':e.day-timedelta(days=1)})
    assert len(module.curve_representatives(weekend,e.day))==2
    for invalid in (median.model_copy(update={'component_snapshot_ids':[]}),
                    median.model_copy(update={'duration_years':D(0)})):
        with pytest.raises(ValueError): module.curve_representatives(curve.model_copy(update={'nodes':[invalid,curve.nodes[1]]}),e.day)


def test_exact_node_and_generic_dv01_unavailable_still_dirty(benchmark_env,monkeypatch):
    e=benchmark_env; original=module.BondModifiedDurationService.build_for_bond
    def modified(*a,**k):
        value=original(*a,**k)
        return value.model_copy(update={'modified_duration_years':D(2)}) if value.bond_id==10 else value
    monkeypatch.setattr(module.BondModifiedDurationService,'build_for_bond',modified)
    original_dirty=module.BondDv01Service.build_for_bond
    monkeypatch.setattr(module.BondDv01Service,'build_for_bond',lambda *a,**k:original_dirty(*a,**k).model_copy(update={'status':'MODIFIED_DURATION_UNAVAILABLE'}))
    result=build(e)
    assert result.status=='READY',result.blockers
    assert result.matching_mode=='EXACT_DURATION_NODE' and len(result.components)==1 and result.dirty_value_call_count==1
    assert result.components[0].genesis_market_evidence.status!='READY'


@pytest.mark.parametrize('amount,currency',[(True,'RUB'),(D('-1'),'RUB'),(D('NaN'),'RUB'),(D('Infinity'),'RUB'),(D(1),'USD')])
def test_invalid_synthetic_cash_values_fail_closed(benchmark_env,amount,currency):
    e=benchmark_env;g=build(e);day=e.day+timedelta(days=1)
    f=BenchmarkCashflowV1(source_event_id=1,bond_id=10,event_date=day,event_type='coupon',source='moex',currency='RUB',amount=D(1))
    with pytest.raises(ValueError): e.service._value(g,day,(f.model_copy(update={'amount':amount,'currency':currency}),),[],set())


def test_zero_previous_nav_and_freshness_failure_preserve_nulls(benchmark_env):
    e=benchmark_env; g=build(e)
    stale=e.service.build_daily(genesis=g,as_of_date=e.day+timedelta(days=8))
    assert stale.status=='UNAVAILABLE' and stale.benchmark_nav_rub is None
    with e.factory() as db:
        for bond in (10,20):
            db.add(BondCashflowEvent(bond_id=bond,event_date=e.day+timedelta(days=1),event_type='redemption',source='moex',currency='RUB',amount=D(0)))
        db.commit()
    zero=e.service.build_daily(genesis=g,as_of_date=e.day+timedelta(days=2))
    assert zero.status=='UNAVAILABLE' and zero.blockers==('BENCHMARK_PREVIOUS_NAV_ZERO',)
    assert zero.benchmark_nav_rub is zero.previous_nav_rub is zero.cumulative_return is None
    assert zero.dirty_value_call_count==0


@pytest.mark.parametrize('field,value',[('horizon_calendar_days',89),('benchmark_type','OTHER'),
    ('strategy_rebalance_policy','DAILY'),('strategy_cashflow_reinvestment_policy','YES'),
    ('benchmark_rebalance_policy','DAILY'),('benchmark_cashflow_reinvestment_policy','YES'),
    ('benchmark_allocation','LOTS'),('primary_success_rule','EXCESS_ONLY'),('benchmark_max_market_age_days',8),
    ('benchmark_max_curve_age_days',8),('benchmark_duration_basis','MACAULAY'),('unexpected',True)])
def test_frozen_policy_has_no_overrides(field,value):
    with pytest.raises(ValidationError): ShadowExperimentPolicyV1(**{field:value})


@pytest.mark.parametrize('field,value',[('pit_ready',0),('require_common_genesis_trade_date',1),('horizon_calendar_days',D(90))])
def test_literal_types_do_not_coerce(field,value):
    with pytest.raises(ValidationError): ShadowExperimentPolicyV1(**{field:value})


def test_nonfinite_dependency_evidence_is_stably_blocked(benchmark_env,monkeypatch):
    e=benchmark_env; original=module.BondModifiedDurationService.build_for_bond
    monkeypatch.setattr(module.BondModifiedDurationService,'build_for_bond',lambda *a,**k:
        original(*a,**k).model_copy(update={'modified_duration_years':D('NaN')}))
    result=build(e)
    assert result.status=='BLOCKED' and result.modified_duration_call_count==2
    assert result.dirty_value_call_count==0 and result.modified_duration_inputs==()
    assert type(result).model_validate_json(result.model_dump_json())==result
    monkeypatch.setattr(module.BondModifiedDurationService,'build_for_bond',original)
    g=build(e); dirty=module.BondDv01Service.build_for_bond
    monkeypatch.setattr(module.BondDv01Service,'build_for_bond',lambda *a,**k:
        dirty(*a,**k).model_copy(update={'dirty_value_currency':D('NaN')}))
    daily=e.service.build_daily(genesis=g,as_of_date=e.day+timedelta(days=1))
    assert daily.status=='UNAVAILABLE' and daily.benchmark_nav_rub is None
    assert daily.dirty_value_call_count==1
    assert type(daily).model_validate_json(daily.model_dump_json())==daily
