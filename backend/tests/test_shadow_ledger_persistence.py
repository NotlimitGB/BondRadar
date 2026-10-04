"""Lossless storage, FK retention, transaction failures and isolation boundaries."""
import ast
from pathlib import Path
from decimal import Decimal
from sqlalchemy import select, func, event
from sqlalchemy.exc import IntegrityError
import pytest
from app.models.shadow_test_run import ShadowTestRun
from app.models.shadow_daily_snapshot import ShadowDailySnapshot
from app.models.shadow_ledger_entry import ShadowLedgerEntry
from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.schemas.shadow_ledger import ShadowAudit, ShadowGenesisPlanV1
from app.services.shadow_ledger_genesis_service import ShadowLedgerGenesisService
from app.services import shadow_ledger_repository as repository
from test_shadow_ledger_genesis import environment, genesis, authorization


def test_storage_exact_return_roundtrip_and_frozen_contract(environment):
    plan,_=genesis(environment); factory,_,_=environment
    value=Decimal("0.3333333333333333333333333333")
    # Isolated storage test: direct fixture write is not an application history API.
    with factory() as db:
        s=db.scalar(select(ShadowDailySnapshot)); s.daily_return=value; db.commit()
    with factory() as db:
        assert db.scalar(select(ShadowDailySnapshot.daily_return))==value
    assert ShadowGenesisPlanV1.model_validate_json(plan.model_dump_json()).model_dump()==plan.model_dump()
    with pytest.raises(ValueError): plan.status="BLOCKED"
    with pytest.raises(ValueError): ShadowGenesisPlanV1(**plan.model_dump(),extra=True)
    assert repository.digest(Decimal("10.000"))==repository.digest(Decimal("10"))
    assert plan.capabilities.broker_execution_ready is False


@pytest.mark.parametrize("model",[ShadowTestRun,Bond,BondMarketSnapshot])
def test_foreign_keys_prevent_history_disappearance(environment,model):
    genesis(environment); factory,_,_=environment
    with factory() as db:
        obj=db.scalar(select(model))
        db.delete(obj)
        with pytest.raises(IntegrityError): db.flush()
        db.rollback()


def test_duplicate_event_constraint_and_shadow_pk_free_hash(environment):
    plan,_=genesis(environment); factory,_,_=environment
    with factory() as db:
        entry=db.scalar(select(ShadowLedgerEntry))
        data={c.name:getattr(entry,c.name) for c in ShadowLedgerEntry.__table__.columns if c.name not in ("id","created_at")}
        db.add(ShadowLedgerEntry(**data))
        with pytest.raises(IntegrityError): db.flush()
        db.rollback()
        state=repository.read_state(db,plan.run_key_sha256)
        changed={k:dict(v) if k=="run" else [dict(r) for r in v] for k,v in state.items()}
        changed["run"]["id"]=999
        for table in ("ledger","snapshots","positions"):
            for row in changed[table]: row["shadow_run_id"]=999
        assert repository.state_hash(state)==repository.state_hash(changed)


def test_post_commit_failure_is_not_rollback(environment,monkeypatch):
    factory,request,_=environment; service=ShadowLedgerGenesisService(factory)
    p=service.plan(request=request); original=repository.audit; calls=[]
    def audit(db,key,expected):
        calls.append(True)
        result=original(db,key,expected)
        return result if len(calls)==1 else result.model_copy(update={"status":"FAILED","blockers":("SHADOW_HISTORY_INVALID",)})
    monkeypatch.setattr(repository,"audit",audit)
    receipt=service.apply(request=request,reviewed_plan=p,authorization=authorization(p))
    assert receipt.status=="POST_COMMIT_AUDIT_FAILED" and receipt.committed_rows==9
    with factory() as db: assert db.scalar(select(func.count()).select_from(ShadowTestRun))==1


def test_commit_outcome_unknown_never_claims_rollback(environment):
    factory,request,_=environment
    p=ShadowLedgerGenesisService(factory).plan(request=request)
    def ambiguous_factory():
        db=factory(); original=db.commit
        def commit():
            original()
            raise RuntimeError("acknowledgement lost")
        db.commit=commit
        return db
    receipt=ShadowLedgerGenesisService(ambiguous_factory).apply(request=request,reviewed_plan=p,authorization=authorization(p))
    assert receipt.status=="COMMIT_OUTCOME_UNKNOWN" and receipt.committed_rows is None
    with factory() as db: assert db.scalar(select(func.count()).select_from(ShadowTestRun))==1


def test_lock_refusal_preserves_database(environment):
    factory,request,engine=environment; service=ShadowLedgerGenesisService(factory)
    p=service.plan(request=request)
    @event.listens_for(engine,"before_cursor_execute")
    def refuse(conn,cursor,statement,parameters,context,many):
        if statement=="BEGIN IMMEDIATE": raise RuntimeError("synthetic busy")
    receipt=service.apply(request=request,reviewed_plan=p,authorization=authorization(p))
    assert receipt.status=="ROLLED_BACK" and receipt.committed_rows==0
    with factory() as db: assert db.scalar(select(func.count()).select_from(ShadowTestRun))==0


def test_no_network_legacy_mutation_or_helper_commit():
    root=Path(__file__).resolve().parents[1]/"app"/"services"
    for name in ("shadow_ledger_repository.py","shadow_ledger_genesis_service.py","shadow_daily_cycle_service.py"):
        tree=ast.parse((root/name).read_text(encoding="utf-8"))
        imports=[ast.unparse(n) for n in ast.walk(tree) if isinstance(n,(ast.Import,ast.ImportFrom))]
        assert not any(token in text for text in imports for token in
            ("httpx","requests","urllib","moex_iss","moex_cashflow_service","tinvest","paper_","ml_"))
        calls=[n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)]
        assert not set(calls)&{"delete","sync","update","getenv","environ","request"}
        if name!="shadow_ledger_repository.py": assert "commit" not in calls and "flush" not in calls


def test_postgresql_ddl_uses_unscaled_numeric_and_restrict():
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects import postgresql
    for model in repository.TABLES:
        sql=str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
        assert "NUMERIC" in sql and "NUMERIC(" not in sql
        if model is ShadowTestRun:
            assert "genesis_date + 90" in sql and "date(genesis_date" not in sql
        else:
            assert "ON DELETE RESTRICT" in sql


def test_horizon_and_negative_nav_database_constraints(environment):
    genesis(environment); factory,_,_=environment
    with factory() as db:
        run=db.scalar(select(ShadowTestRun)); run.horizon_days=91
        with pytest.raises(IntegrityError): db.flush()
        db.rollback()
        snapshot=db.scalar(select(ShadowDailySnapshot)); snapshot.nav_rub=Decimal(-1)
        with pytest.raises(IntegrityError): db.flush()
        db.rollback()
