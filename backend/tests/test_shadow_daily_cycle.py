"""Calendar continuity, cashflow accounting, dirty marks and immutable history."""
from datetime import timedelta, datetime, date
from decimal import Decimal, localcontext, ROUND_UP
import pytest
from sqlalchemy import select, event
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.shadow_test_run import ShadowTestRun
from app.models.shadow_daily_snapshot import ShadowDailySnapshot
from app.schemas.shadow_ledger import ShadowDailyAuthorizationV1
from app.services.shadow_daily_cycle_service import ShadowDailyCycleService
from app.services.shadow_daily_cycle_service import validate_mark, validate_cashflows
from app.services import shadow_ledger_repository as repository
from test_shadow_ledger_genesis import environment, genesis

D=Decimal


@pytest.mark.parametrize("amount",[D(-1),D("NaN"),D("Infinity"),True,1,"1"])
def test_synthetic_cashflow_invalid_types_and_values(amount):
    row=dict(bond_id=1,event_date=date(2026,10,1),event_type="coupon",source="moex",currency="RUB",amount=amount)
    with pytest.raises(ValueError,match="CASHFLOW_EVIDENCE_INVALID"): validate_cashflows([row])


def test_zero_cashflow_and_synthetic_duplicate():
    row=dict(bond_id=1,event_date=date(2026,10,1),event_type="coupon",source="moex",currency="RUB",amount=D(0))
    validate_cashflows([row])
    with pytest.raises(ValueError,match="CASHFLOW_DUPLICATE"): validate_cashflows([row,row.copy()])


def authorization(plan):
    return ShadowDailyAuthorizationV1(explicit_apply=True,**{name:getattr(plan,name) for name in
        ("plan_sha256","run_key_sha256","current_shadow_db_state_sha256","input_state_sha256")})


def flow(factory,bond,day,kind,amount,currency="RUB",source="moex"):
    with factory() as db:
        db.add(BondCashflowEvent(bond_id=bond,event_date=day,event_type=kind,amount=amount,currency=currency,source=source))
        db.commit()


def test_real_dirty_mark_even_when_duration_unavailable_and_context(environment):
    p,_=genesis(environment); factory,_,engine=environment
    service=ShadowDailyCycleService(factory); day=p.as_of_date+timedelta(days=1)
    sql=[]; event.listen(engine,"before_cursor_execute",lambda c,cur,s,params,ctx,m:sql.append(s))
    plan=service.plan(run_key_sha256=p.run_key_sha256,as_of_date=day)
    assert plan.status=="EXECUTABLE", plan.blockers
    assert all(v.status!="READY" and v.availability.has_dirty_value for v in plan.valuation_evidence)
    assert all(v.provenance.max_market_age_days==7 for v in plan.valuation_evidence)
    assert plan.snapshot.nav_rub==p.snapshot.nav_rub and plan.snapshot.daily_return==0
    assert all(s.lstrip().upper().startswith("SELECT") for s in sql)
    with localcontext() as ctx:
        ctx.prec=5; ctx.rounding=ROUND_UP
        again=service.plan(run_key_sha256=p.run_key_sha256,as_of_date=day)
        assert ctx.prec==5
    assert again.model_dump()==plan.model_dump()
    receipt=service.apply(reviewed_plan=plan,authorization=authorization(plan))
    assert receipt.status=="APPLIED", receipt
    repeat=service.plan(run_key_sha256=p.run_key_sha256,as_of_date=day)
    assert repeat.status=="IDEMPOTENT_NOOP"
    assert service.apply(reviewed_plan=repeat,authorization=authorization(repeat)).status=="IDEMPOTENT_NOOP"


def test_coupon_amortization_redemption_order_cash_and_redeemed_continuity(environment):
    p,_=genesis(environment); factory,_,_=environment
    day=p.as_of_date+timedelta(days=1); q=p.positions[0].quantity
    for kind,amount in (("redemption",D(1000)),("coupon",D(40)),("amortization",D(100))):
        flow(factory,1,day,kind,amount)
    service=ShadowDailyCycleService(factory)
    plan=service.plan(run_key_sha256=p.run_key_sha256,as_of_date=day)
    assert plan.status=="EXECUTABLE",plan.blockers
    assert [e.event_type for e in plan.events]==["COUPON","AMORTIZATION","REDEMPTION"]
    assert [e.cash_delta_rub for e in plan.events]==[q*D(40),q*D(100),q*D(1000)]
    assert [e.quantity_delta for e in plan.events]==[0,0,-q]
    assert plan.positions[0].position_status=="REDEEMED" and plan.positions[0].market_value_rub==0
    assert [v.bond_id for v in plan.valuation_evidence]==[2,3]
    assert plan.snapshot.cash_rub==p.snapshot.cash_rub+q*D(1140)
    with localcontext(repository.DECIMAL_CONTEXT):
        assert plan.snapshot.daily_return==plan.snapshot.nav_rub/p.snapshot.nav_rub-1
        assert plan.snapshot.cumulative_return==plan.snapshot.nav_rub/p.snapshot.nav_rub-1
    assert service.apply(reviewed_plan=plan,authorization=authorization(plan)).status=="APPLIED"
    future=service.plan(run_key_sha256=p.run_key_sha256,as_of_date=day+timedelta(days=1))
    assert future.status=="EXECUTABLE" and future.positions[0].quantity==0
    assert future.positions[0].dirty_value_rub_per_bond is None


@pytest.mark.parametrize("kind,amount,currency,blocker",[
    ("coupon",None,"RUB","CASHFLOW_AMOUNT_MISSING"),
    ("coupon",D(1),"USD","CASHFLOW_EVIDENCE_INVALID"),
    ("other",D(1),"RUB","UNKNOWN_CASHFLOW_EVENT"),
])
def test_cashflow_failures_no_mutation(environment,kind,amount,currency,blocker):
    p,_=genesis(environment); factory,_,_=environment; day=p.as_of_date+timedelta(days=1)
    flow(factory,1,day,kind,amount,currency)
    result=ShadowDailyCycleService(factory).plan(run_key_sha256=p.run_key_sha256,as_of_date=day)
    assert result.status=="BLOCKED" and result.blockers==(blocker,)


def test_offer_and_nonmoex_are_not_auto_exercised(environment):
    p,_=genesis(environment); factory,_,_=environment; day=p.as_of_date+timedelta(days=1)
    flow(factory,1,day,"offer_redemption",None)
    flow(factory,2,day,"coupon",D(99),source="manual")
    result=ShadowDailyCycleService(factory).plan(run_key_sha256=p.run_key_sha256,as_of_date=day)
    assert result.status=="EXECUTABLE" and result.events==()
    assert result.diagnostics[0]=="DV01_STATUS_"+result.valuation_evidence[0].status
    assert "OFFER_REDEMPTION_NOT_AUTO_EXERCISED" in result.diagnostics


def test_gap_backward_horizon_and_invalid_context(environment):
    p,_=genesis(environment); factory,_,_=environment; service=ShadowDailyCycleService(factory)
    for day,reason in ((p.as_of_date,"DATE_NOT_FORWARD"),(p.as_of_date+timedelta(days=2),"DAILY_SEQUENCE_GAP"),
        (p.planned_end_date+timedelta(days=1),"HORIZON_EXCEEDED")):
        assert service.plan(run_key_sha256=p.run_key_sha256,as_of_date=day).blockers==(reason,)
    assert service.plan(run_key_sha256=p.run_key_sha256,as_of_date=datetime.now()).blockers==("INPUT_INVALID",)


def test_historical_cashflow_drift_never_rewrites(environment):
    p,_=genesis(environment); factory,_,_=environment; day=p.as_of_date+timedelta(days=1)
    flow(factory,1,day,"coupon",D(40))
    service=ShadowDailyCycleService(factory); plan=service.plan(run_key_sha256=p.run_key_sha256,as_of_date=day)
    assert service.apply(reviewed_plan=plan,authorization=authorization(plan)).status=="APPLIED"
    with factory() as db:
        before=db.scalar(select(ShadowDailySnapshot.snapshot_sha256).where(ShadowDailySnapshot.as_of_date==day))
        row=db.scalar(select(BondCashflowEvent)); row.amount=D(41); db.commit()
    changed=service.plan(run_key_sha256=p.run_key_sha256,as_of_date=day)
    assert changed.blockers==("HISTORICAL_SOURCE_DRIFT",)
    with factory() as db:
        assert db.scalar(select(ShadowDailySnapshot.snapshot_sha256).where(ShadowDailySnapshot.as_of_date==day))==before


@pytest.mark.parametrize("change",["dirty_zero","dirty_negative","dirty_nonfinite","bool_id","currency","nominal",
    "availability","age","date","snapshot","profile","version","pit","identity"])
def test_dirty_value_integrity_fail_closed(environment,change):
    p,_=genesis(environment); factory,_,_=environment; day=p.as_of_date+timedelta(days=1)
    view=ShadowDailyCycleService(factory).plan(run_key_sha256=p.run_key_sha256,as_of_date=day).valuation_evidence[0]
    updates={}
    if change.startswith("dirty_"): updates["dirty_value_currency"]={"dirty_zero":D(0),"dirty_negative":D(-1),"dirty_nonfinite":D("NaN")}[change]
    if change=="bool_id": updates["bond_id"]=True
    if change=="currency": updates["currency_code"]="USD"
    if change=="nominal": updates["nominal_state"]="unknown"
    if change=="availability": updates["availability"]=view.availability.model_copy(update={"has_dirty_value":False})
    if change=="age": updates["market_age_days"]=8
    if change=="date": updates["as_of_date"]=day+timedelta(days=1)
    if change=="snapshot": updates["market_snapshot_id"]=0
    if change=="profile": updates["provenance"]=view.provenance.model_copy(update={"security_master_profile_id":None})
    if change=="version": updates["contract_version"]="bad"
    if change=="pit": updates["pit_ready"]=True
    if change=="identity": updates["provenance"]=view.provenance.model_copy(update={"market_identity":view.provenance.market_identity.model_copy(update={"bond_id":99})})
    with pytest.raises(ValueError): validate_mark(view.model_copy(update=updates),1,day)


def test_freshness_boundary_and_market_source_drift(environment):
    from app.models.bond_market_snapshot import BondMarketSnapshot
    p,_=genesis(environment); factory,_,_=environment; service=ShadowDailyCycleService(factory)
    first=None
    for elapsed in range(1,8):
        plan=service.plan(run_key_sha256=p.run_key_sha256,as_of_date=p.as_of_date+timedelta(days=elapsed))
        assert plan.status=="EXECUTABLE",plan.blockers
        assert plan.valuation_evidence[0].market_age_days==elapsed
        assert service.apply(reviewed_plan=plan,authorization=authorization(plan)).status=="APPLIED"
        if first is None: first=plan
    stale=service.plan(run_key_sha256=p.run_key_sha256,as_of_date=p.as_of_date+timedelta(days=8))
    assert stale.status=="BLOCKED"
    with factory() as db:
        market=db.scalar(select(BondMarketSnapshot).where(BondMarketSnapshot.bond_id==1)); market.nkd=D(2); db.commit()
    historical=service.plan(run_key_sha256=p.run_key_sha256,as_of_date=first.as_of_date)
    assert historical.blockers==("HISTORICAL_SOURCE_DRIFT",)


def test_90_calendar_day_horizon_all_redeemed_no_future_price_calls(environment):
    p,_=genesis(environment); factory,_,_=environment; day=p.as_of_date+timedelta(days=1)
    for i in (1,2,3): flow(factory,i,day,"redemption",D(1000))
    service=ShadowDailyCycleService(factory)
    for elapsed in range(1,91):
        plan=service.plan(run_key_sha256=p.run_key_sha256,as_of_date=p.as_of_date+timedelta(days=elapsed))
        assert plan.status=="EXECUTABLE",plan.blockers
        assert not plan.valuation_evidence and plan.snapshot.active_position_count==0 and plan.snapshot.redeemed_position_count==3
        assert service.apply(reviewed_plan=plan,authorization=authorization(plan)).status=="APPLIED"
    with factory() as db:
        run=db.scalar(select(ShadowTestRun)); assert run.status=="OBSERVATION_COMPLETE" and run.observation_completed_at is not None
    again=service.plan(run_key_sha256=p.run_key_sha256,as_of_date=p.planned_end_date)
    assert again.status=="IDEMPOTENT_NOOP"


def test_valid_zero_nav_blocks_next_daily_return(environment):
    from app.models.bond import Bond
    from app.models.bond_market_snapshot import BondMarketSnapshot
    from app.models.bond_security_master_profile import BondSecurityMasterProfile
    from app.models.company import Company
    from app.schemas.shadow_ledger import ShadowGenesisRequestV1
    from app.services.shadow_ledger_genesis_service import ShadowLedgerGenesisService
    from app.services.shadow_execution_planner import ShadowExecutionPlanner
    from test_shadow_ledger_genesis import unique_identities, authorization as genesis_authorization
    from test_shadow_execution_terms_loader import strategy, terms
    factory,_,_=environment
    source=unique_identities(strategy(n=5,overrides={i:{"dirty_value_currency":D(1000),"clean_price":D("99.85"),
        "clean_quote_pct":D("99.85"),"clean_value_currency":D("998.5")} for i in range(1,6)}))
    shadow=ShadowExecutionPlanner.build(source,terms(source))
    with factory() as db:
        company=db.scalar(select(Company))
        for pos in shadow.positions[:3]:
            market=db.get(BondMarketSnapshot,pos.market_snapshot_id)
            market.clean_price=pos.clean_quote_pct; market.nkd=pos.nkd_currency
        for pos in shadow.positions[3:]:
            db.add(Bond(id=pos.bond_id,company_id=company.id,isin=pos.isin,secid=pos.secid,name="Isolated zero NAV test"))
        db.flush()
        for pos in shadow.positions[3:]:
            db.add(BondSecurityMasterProfile(id=pos.security_master_profile_id,bond_id=pos.bond_id,
                currency_state="verified",currency_code="RUB",nominal_state="verified",nominal_value=pos.nominal_value,
                lot_size_state="verified",lot_size=1,trading_board_state="verified",trading_board="TQCB"))
            db.add(BondMarketSnapshot(id=pos.market_snapshot_id,bond_id=pos.bond_id,trade_date=pos.market_trade_date,source="moex",
                clean_price=pos.clean_quote_pct,nkd=pos.nkd_currency))
        db.commit()
    request=ShadowGenesisRequestV1(shadow_execution=shadow,source_code_sha="b"*40)
    service=ShadowLedgerGenesisService(factory); p=service.plan(request=request)
    assert p.status=="EXECUTABLE" and p.snapshot.cash_rub==0
    assert service.apply(request=request,reviewed_plan=p,authorization=genesis_authorization(p)).status=="APPLIED"
    day=p.as_of_date+timedelta(days=1)
    for i in range(1,6): flow(factory,i,day,"redemption",D(0))
    daily=ShadowDailyCycleService(factory); mark=daily.plan(run_key_sha256=p.run_key_sha256,as_of_date=day)
    assert mark.status=="EXECUTABLE" and mark.snapshot.nav_rub==0 and mark.snapshot.daily_return==D(-1)
    assert daily.apply(reviewed_plan=mark,authorization=authorization(mark)).status=="APPLIED"
    assert daily.plan(run_key_sha256=p.run_key_sha256,as_of_date=day+timedelta(days=1)).blockers==("PREVIOUS_NAV_ZERO",)
