"""Isolated, explicitly authorized Shadow genesis critical paths."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, localcontext, ROUND_UP
import pytest
from sqlalchemy import create_engine, event, select, func
from sqlalchemy.orm import sessionmaker
from pydantic import BaseModel
from functools import lru_cache
from app.db.base import Base
from app.models.company import Company
from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_security_master_profile import BondSecurityMasterProfile
from app.models.shadow_test_run import ShadowTestRun
from app.models.shadow_ledger_entry import ShadowLedgerEntry
from app.models.shadow_daily_snapshot import ShadowDailySnapshot
from app.models.shadow_daily_position_snapshot import ShadowDailyPositionSnapshot
from app.schemas.shadow_ledger import ShadowGenesisRequestV1, ShadowGenesisAuthorizationV1
from app.services.shadow_execution_planner import ShadowExecutionPlanner
from app.services.shadow_ledger_genesis_service import ShadowLedgerGenesisService
from app.services import shadow_ledger_repository as repository
from test_shadow_execution_terms_loader import strategy, terms

D = Decimal


def unique_identities(value,bond_id=None):
    """Give the synthetic upstream graph genuine distinct persisted identities."""
    if isinstance(value,BaseModel):
        bond_id=getattr(value,"bond_id",bond_id)
        fields={name:unique_identities(getattr(value,name),bond_id) for name in type(value).model_fields}
        bond_id=fields.get("bond_id",bond_id)
        if type(bond_id) is int:
            if "isin" in fields: fields["isin"]=f"RU303{bond_id:07d}"
            if "secid" in fields: fields["secid"]=f"SHADOW303_{bond_id}"
            for name,field in list(fields.items()):
                if name.endswith("snapshot_id") and type(field) is int:
                    fields[name]=1000+bond_id*10+field
                elif name.endswith("snapshot_ids") and isinstance(field,(tuple,list)):
                    fields[name]=type(field)(1000+bond_id*10+i for i in field)
        return value.model_copy(update=fields)
    if isinstance(value,tuple): return tuple(unique_identities(v,bond_id) for v in value)
    if isinstance(value,list): return [unique_identities(v,bond_id) for v in value]
    return value


@lru_cache(maxsize=1)
def shadow_source():
    s = unique_identities(strategy(overrides={i:{"dirty_value_currency":D(1020),"clean_price":D("101.85"),
        "clean_quote_pct":D("101.85"),"clean_value_currency":D("1018.5")} for i in (1,2,3)}))
    return ShadowExecutionPlanner.build(s,terms(s))


@pytest.fixture
def environment(tmp_path):
    engine = create_engine("sqlite+pysqlite:///"+str(tmp_path/"shadow.sqlite"))
    @event.listens_for(engine,"connect")
    def foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine,autoflush=True)
    shadow = shadow_source()
    with factory() as db:
        company = Company(name="Isolated Task303 issuer",ticker="TASK303")
        db.add(company); db.flush()
        for p in shadow.positions:
            db.add(Bond(id=p.bond_id,company_id=company.id,name="Isolated Shadow bond",isin=p.isin,secid=p.secid))
        db.flush()
        for p in shadow.positions:
            db.add(BondSecurityMasterProfile(id=p.security_master_profile_id,bond_id=p.bond_id,
                currency_state="verified",currency_code="RUB",nominal_state="verified",nominal_value=p.nominal_value,
                lot_size_state="verified",lot_size=p.lot_size,trading_board_state="verified",trading_board="TQCB"))
            db.add(BondMarketSnapshot(id=p.market_snapshot_id,bond_id=p.bond_id,trade_date=p.market_trade_date,
                source="moex",clean_price=p.clean_quote_pct,nkd=p.nkd_currency,duration_years=D(2),yield_to_maturity=D(12)))
        db.commit()
    request = ShadowGenesisRequestV1(shadow_execution=shadow,source_code_sha="a"*40)
    yield factory, request, engine
    engine.dispose()


def authorization(plan):
    return ShadowGenesisAuthorizationV1(explicit_apply=True,**{field:getattr(plan,field) for field in
        ("plan_sha256","run_key_sha256","current_shadow_db_state_sha256","input_state_sha256",
         "shadow_execution_sha256","source_universe_sha256","source_code_sha")})


def genesis(environment):
    factory, request, _ = environment
    service = ShadowLedgerGenesisService(factory)
    plan = service.plan(request=request)
    assert plan.status == "EXECUTABLE", plan.blockers
    receipt = service.apply(request=request,reviewed_plan=plan,authorization=authorization(plan))
    assert receipt.status == "APPLIED", receipt
    return plan, receipt


def test_plan_select_only_deterministic_context_and_immutability(environment):
    factory, request, engine = environment
    statements = []
    event.listen(engine,"before_cursor_execute",lambda c,cur,sql,p,ctx,m:statements.append(sql))
    before = deepcopy(request.model_dump())
    service = ShadowLedgerGenesisService(factory)
    first = service.plan(request=request)
    with localcontext() as ctx:
        ctx.prec=5; ctx.rounding=ROUND_UP
        second = service.plan(request=request)
        assert ctx.prec == 5 and ctx.rounding == ROUND_UP
    assert first.status == "EXECUTABLE", first.blockers
    assert first.model_dump_json() == second.model_dump_json()
    assert request.model_dump() == before
    assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    assert first.snapshot.nav_rub == request.shadow_execution.summary.capital_rub
    assert first.snapshot.cash_rub+first.snapshot.market_value_rub == first.snapshot.nav_rub
    assert len(first.events)==4 and [e.event_type for e in first.events]==["INITIAL_CAPITAL"]+["GENESIS_PURCHASE"]*3
    assert first.planned_end_date==first.as_of_date+timedelta(days=90)


@pytest.mark.parametrize("change",["status","flags","risk","terms","zero","cash","quantity","board","code","date","provenance"])
def test_genesis_fail_closed_before_writes(environment,change):
    factory, request, _ = environment
    s=request.shadow_execution
    if change=="status": s=s.model_copy(update={"status":"PARTIAL"})
    if change=="flags": s=s.model_copy(update={"quality_flags":("TARGET_BELOW_ONE_LOT",)})
    if change=="risk": s=s.model_copy(update={"post_rounding_risk_evaluation":s.post_rounding_risk_evaluation.model_copy(update={"status":"BLOCKED"})})
    if change=="terms": s=s.model_copy(update={"execution_terms_batch":s.execution_terms_batch.model_copy(update={"unavailable_count":1})})
    if change=="zero": s=s.model_copy(update={"summary":s.summary.model_copy(update={"zero_lot_position_count":1})})
    if change=="cash": s=s.model_copy(update={"summary":s.summary.model_copy(update={"shadow_cash_rub":D(1)})})
    if change in ("quantity","board"):
        p=s.positions[0].model_copy(update={"planned_bond_quantity":0} if change=="quantity" else {"trading_board":"OTHER"})
        s=s.model_copy(update={"positions":(p,*s.positions[1:])})
    if change=="date": s=s.model_copy(update={"as_of_date":s.as_of_date+timedelta(days=1)})
    if change=="provenance": s=s.model_copy(update={"provenance":s.provenance.model_copy(update={"selected_bond_ids":()})})
    request=request.model_copy(update={"shadow_execution":s,"source_code_sha":"bad" if change=="code" else request.source_code_sha})
    plan=ShadowLedgerGenesisService(factory).plan(request=request)
    assert plan.status=="BLOCKED"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ShadowTestRun))==0


def test_apply_atomic_exact_accounting_commit_once_and_replay(environment):
    factory, request, engine = environment
    commits=[]
    event.listen(engine,"commit",lambda conn:commits.append(True))
    plan, receipt=genesis(environment)
    assert len(commits)==1 and receipt.pre_commit_audit.status==receipt.post_commit_audit.status=="VERIFIED"
    assert receipt.committed_rows==9
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ShadowTestRun))==1
        assert db.scalar(select(func.count()).select_from(ShadowLedgerEntry))==4
        assert db.scalar(select(func.count()).select_from(ShadowDailySnapshot))==1
        assert db.scalar(select(func.count()).select_from(ShadowDailyPositionSnapshot))==3
        assert sum(e.cash_delta_rub for e in db.scalars(select(ShadowLedgerEntry)))==plan.snapshot.cash_rub
    service=ShadowLedgerGenesisService(factory)
    assert service.apply(request=request,reviewed_plan=plan,authorization=authorization(plan)).status=="ROLLED_BACK"
    again=service.plan(request=request)
    assert again.status=="IDEMPOTENT_NOOP"
    noop=service.apply(request=request,reviewed_plan=again,authorization=authorization(again))
    assert noop.status=="IDEMPOTENT_NOOP" and noop.committed_rows==0 and len(commits)==1


def test_authorization_tamper_pending_state_and_rollback(environment,monkeypatch):
    factory, request, _=environment
    service=ShadowLedgerGenesisService(factory); p=service.plan(request=request)
    bad=authorization(p).model_copy(update={"plan_sha256":"0"*64})
    assert service.apply(request=request,reviewed_plan=p,authorization=bad).status=="BLOCKED"
    assert service.apply(request=request,reviewed_plan=p.model_copy(update={"plan_sha256":"0"*64}),authorization=authorization(p)).status=="BLOCKED"
    original=repository.persist
    def failing(db,plan,run_fields):
        original(db,plan,run_fields)
        raise RuntimeError("synthetic failure after insert")
    monkeypatch.setattr(repository,"persist",failing)
    result=service.apply(request=request,reviewed_plan=p,authorization=authorization(p))
    assert result.status=="ROLLED_BACK" and result.committed_rows==0
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ShadowTestRun))==0
    caller=factory(); caller.add(Company(name="Caller pending",ticker="PENDING"))
    assert ShadowLedgerGenesisService(lambda:caller).plan(request=request).blockers==("SESSION_NOT_FRESH",)
    assert caller.new
    caller.rollback(); caller.close()


def test_separate_provenance_persistence_state_hash_and_noop(environment):
    from app.services.shadow_ledger_genesis_service import original_genesis_plan
    factory, request, _ = environment
    plan, _ = genesis(environment)
    with factory() as db:
        run = db.scalar(select(ShadowTestRun))
        assert run.shadow_execution_sha256 == plan.shadow_execution_sha256
        assert run.genesis_plan_sha256 == plan.plan_sha256
        assert run.genesis_plan_sha256 != run.shadow_execution_sha256
        assert "genesis_shadow_plan_sha256" not in run.__table__.columns
        state = repository.read_state(db, plan.run_key_sha256)
        for field in ("shadow_execution_sha256", "genesis_plan_sha256"):
            changed = deepcopy(state); changed["run"][field] = "0" * 64
            assert repository.state_hash(changed) != repository.state_hash(state)
    service = ShadowLedgerGenesisService(factory)
    repeat = service.plan(request=request)
    assert repeat.status == "IDEMPOTENT_NOOP" and repeat.plan_sha256 != plan.plan_sha256
    assert original_genesis_plan(repeat) == plan
    receipt = service.apply(request=request, reviewed_plan=repeat, authorization=authorization(repeat))
    assert receipt.status == "IDEMPOTENT_NOOP" and receipt.committed_rows == 0
    with factory() as db:
        assert db.scalar(select(ShadowTestRun.genesis_plan_sha256)) == plan.plan_sha256


@pytest.mark.parametrize("field", ["plan_sha256", "shadow_execution_sha256"])
def test_provenance_authorization_tampering_blocks_before_transaction(environment, field):
    factory, request, _ = environment
    service = ShadowLedgerGenesisService(factory); plan = service.plan(request=request)
    auth = authorization(plan).model_copy(update={field: "0" * 64})
    assert service.apply(request=request, reviewed_plan=plan, authorization=auth).status == "BLOCKED"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ShadowTestRun)) == 0


@pytest.mark.parametrize("field", ["genesis_plan_sha256", "shadow_execution_sha256", "source_code_sha", "source_universe_sha256"])
def test_persisted_provenance_corruption_fails_audit_and_replay(environment, field):
    factory, request, _ = environment; plan, _ = genesis(environment)
    with factory() as db:
        run = db.scalar(select(ShadowTestRun)); setattr(run, field, "0" * (40 if field == "source_code_sha" else 64)); db.commit()
    with factory() as db:
        assert repository.audit_genesis(db, plan).status == "FAILED"
    repeat = ShadowLedgerGenesisService(factory).plan(request=request)
    assert repeat.status == "BLOCKED" and repeat.blockers == ("HISTORICAL_SOURCE_DRIFT",)


@pytest.mark.parametrize("phase", ["before_commit", "after_commit"])
def test_provenance_audit_guards_both_commit_phases(environment, monkeypatch, phase):
    factory, request, engine = environment
    service = ShadowLedgerGenesisService(factory); plan = service.plan(request=request)
    if phase == "before_commit":
        original = repository.persist
        def corrupt(db, current, run_fields):
            original(db, current, run_fields)
            db.scalar(select(ShadowTestRun)).genesis_plan_sha256 = "0" * 64
            db.flush()
        monkeypatch.setattr(repository, "persist", corrupt)
    else:
        calls = []
        def corrupt_factory():
            db = factory(); calls.append(True)
            if len(calls) == 2:
                # Isolated fixture mutation simulates persisted post-commit corruption.
                with factory() as damaged:
                    damaged.scalar(select(ShadowTestRun)).shadow_execution_sha256 = "0" * 64
                    damaged.commit()
            return db
        service = ShadowLedgerGenesisService(corrupt_factory)
    receipt = service.apply(request=request, reviewed_plan=plan, authorization=authorization(plan))
    assert receipt.status == ("ROLLED_BACK" if phase == "before_commit" else "POST_COMMIT_AUDIT_FAILED")
    assert receipt.committed_rows == (0 if phase == "before_commit" else 9)
