"""Frozen payload reconstruction, normalized projections and corruption gates."""
from decimal import Decimal
import pytest
from sqlalchemy import select
from app.services import shadow_scale_persistence_repository as store
from app.models.shadow_test_run import ShadowTestRun, ShadowDecimal
from test_shadow_scale_activation import activation_env, prepared, authorize, counts


def activated(e):
    p=e.service.plan(request=e.request)
    r=e.service.apply(request=e.request,reviewed_plan=p,authorization=authorize(p))
    assert r.status=="APPLIED",r.blockers
    return r


def test_lossless_full_benchmarks_components_and_semantic_state(activation_env):
    e=activation_env;r=activated(e)
    with e.factory() as db:
        for cid,case in zip(r.case_ids,e.request.scale_genesis.cases):
            value=store.load_persisted_benchmark_genesis(db,experiment_case_id=cid)
            assert value==case.experiment_genesis.benchmark
            assert value.model_dump()==case.experiment_genesis.benchmark.model_dump()
        p=e.service.plan(request=e.request)
        children=tuple(store.ledger.read_state(db,c.child_run_key_sha256) for c in p.cases)
        assert all(s['run']['genesis_plan_sha256']==c.strategy_genesis_plan_sha256 for s,c in zip(children,p.cases))
        # Exact Decimal survives with more fractional places than money scales.
        typ=ShadowDecimal();v=Decimal("1.12345678901234567890123456789")
        assert typ.process_result_value(typ.process_bind_param(v,db.get_bind().dialect),db.get_bind().dialect)==v


@pytest.mark.parametrize('kind',['payload','component','group','run','missing_component','partial_benchmark'])
def test_corrupt_history_is_blocked_without_repair(activation_env,kind):
    e=activation_env;r=activated(e)
    before=e.service.plan(request=e.request)
    with e.factory() as db:
        if kind=='payload':
            b=db.get(store.Benchmark,r.benchmark_ids[0]);payload=dict(b.benchmark_payload_json);payload['genesis_nav_rub']='99';b.benchmark_payload_json=payload
        elif kind=='component':db.scalars(select(store.Component).limit(1)).one().weight=Decimal('0.99')
        elif kind=='group':db.get(store.Group,r.group_id).source_code_sha='b'*40
        elif kind=='run':db.get(ShadowTestRun,r.child_run_ids[0]).genesis_plan_sha256='b'*64
        elif kind=='missing_component':db.delete(db.scalars(select(store.Component).limit(1)).one())
        else:
            b=db.get(store.Benchmark,r.benchmark_ids[0])
            for c in db.scalars(select(store.Component).where(store.Component.benchmark_id==b.id)):db.delete(c)
            db.flush();db.delete(b)
        db.commit()
    current=e.service.plan(request=e.request)
    assert current.status=='BLOCKED' and current.current_db_state_sha256!=before.current_db_state_sha256
    assert any(x in current.blockers for x in ('HISTORICAL_SCALE_STATE_DRIFT','PARTIAL_SCALE_ACTIVATION_STATE'))
    if kind in ('payload','component','missing_component','partial_benchmark'):
        with e.factory() as db,pytest.raises(ValueError,match='BENCHMARK_PERSISTENCE_INVALID'):
            store.load_persisted_benchmark_genesis(db,experiment_case_id=r.case_ids[0])


def test_new_database_ids_excluded_but_source_ids_preserved(activation_env):
    from copy import deepcopy
    e=activation_env;r=activated(e);p=e.service.plan(request=e.request)
    with e.factory() as db:
        state=store.read_state(db,e.request.scale_genesis,p.group_key_sha256,tuple(c.child_run_key_sha256 for c in p.cases))
    before=store.state_hash(state);changed=deepcopy(state)
    for name in ('groups','cases','benchmarks','components'):
        for row in changed[name]:
            row['id']+=1000
            for key in ('experiment_group_id','experiment_case_id','benchmark_id','shadow_run_id'):
                if key in row:row[key]+=1000
    for child in changed['children'].values():
        child['run']['id']+=1000
        for name in ('ledger','snapshots','positions'):
            for row in child[name]:
                row['id']+=1000;row['shadow_run_id']+=1000
                for key in ('previous_snapshot_id','shadow_daily_snapshot_id'):
                    if row.get(key) is not None:row[key]+=1000
    assert store.state_hash(changed)==before
    changed['components'][0]['market_snapshot_id']+=1000
    assert store.state_hash(changed)!=before


def test_restrict_foreign_keys_preserve_activation_graph(activation_env):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    e=activation_env;r=activated(e);before=counts(e)
    with e.factory() as db:
        db.execute(text('PRAGMA foreign_keys=ON'))
        with pytest.raises(IntegrityError):
            db.execute(text('DELETE FROM shadow_experiment_groups WHERE id=:id'),{'id':r.group_id})
        db.rollback()
    assert counts(e)==before
