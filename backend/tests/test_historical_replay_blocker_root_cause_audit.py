"""Task306A1 hermetic RCA and persisted-only curve authority."""
import ast
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext, ROUND_DOWN
from pathlib import Path
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from app.models.bond import Bond
from app.models.company import Company
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_security_master_profile import BondSecurityMasterProfile
from app.models.credit_risk_evidence import CreditRatingEvent
from app.schemas.historical_replay_blocker_root_cause import HistoricalReplayBlockerRootCauseAuditV1 as Audit
from app.services import historical_replay_blocker_root_cause_audit_service as rca
from app.services import modern_historical_replay_readiness_audit_service as old
from test_modern_historical_replay_readiness_audit import seeded, T, D


@contextmanager
def readonly(engine):
    with engine.connect() as connection:
        prior=connection.exec_driver_sql("PRAGMA query_only").scalar_one()
        connection.exec_driver_sql("PRAGMA query_only=ON");connection.rollback()
        connection.exec_driver_sql("BEGIN")
        try:
            with Session(bind=connection,autoflush=False,join_transaction_mode="rollback_only") as db:yield db
        finally:
            connection.rollback()
            connection.exec_driver_sql("PRAGMA query_only=ON" if prior else "PRAGMA query_only=OFF");connection.rollback()


def build(env):
    with readonly(env.engine) as db:return rca.HistoricalReplayBlockerRootCauseAuditService(db).build()


def at(report,day=T):return next(row for row in report.per_date if row.as_of_date==day)


def stage(rows,key):return next(s for s in rows if s.stage==key)


def test_source_binding_exact_funnels_and_determinism(seeded,monkeypatch):
    calls=[];curves=[]
    original=old.ModernHistoricalReplayReadinessAuditService.build
    curve=rca.OfzReferenceCurveService.build_curve
    monkeypatch.setattr(old.ModernHistoricalReplayReadinessAuditService,"build",lambda self:(calls.append(1),original(self))[1])
    def crosscheck(self,day,**kwargs):
        curves.append((day,kwargs));return curve(self,day,**kwargs)
    monkeypatch.setattr(rca.OfzReferenceCurveService,"build_curve",crosscheck)
    with readonly(seeded.engine) as db:
        source=original(old.ModernHistoricalReplayReadinessAuditService(db))
        with localcontext() as ctx:
            ctx.prec=6;ctx.rounding=ROUND_DOWN
            a=rca.HistoricalReplayBlockerRootCauseAuditService(db).build()
            assert ctx.prec==6 and ctx.rounding==ROUND_DOWN
        b=rca.HistoricalReplayBlockerRootCauseAuditService(db).build()
    assert a.status=="COMPLETE",a.blockers
    assert a==b and len(calls)==2 and len(curves)==2*a.candidate_date_count
    assert all(kwargs=={"market_source":"moex","max_curve_age_days":7} for _,kwargs in curves)
    assert a.source_readiness_audit_sha256==source.audit_sha256
    assert a.source_unusable_date_count==source.unusable_date_count
    row=at(a)
    assert tuple(s.stage for s in row.outcome_funnel)==rca.OUTCOME_ORDER
    assert tuple(s.stage for s in row.ofz_funnel)==rca.OFZ_ORDER
    assert tuple(s.stage for s in row.credit_funnel)==rca.CREDIT_ORDER
    assert stage(row.outcome_funnel,"FULL_OBSERVED_OUTCOME").pass_count==5
    assert row.decision_only_intersection_count==row.decision_plus_outcome_intersection_count==3
    assert row.maximum_credit_cohort_size==3 and row.credit_cohorts[0].size_implied_peer_count==2
    assert row.actual_task277_readiness=="NOT_EVALUATED"
    assert stage(row.credit_funnel,"PUBLICATION_PROVEN").kind=="INDEPENDENT_DIAGNOSTIC"
    assert row.credit_proven_cohorts==()
    assert all(s.fail_count==s.input_count-s.pass_count for s in row.outcome_funnel)
    assert Audit.model_validate_json(a.model_dump_json())==a
    with pytest.raises(ValueError):a.status="BLOCKED"
    with pytest.raises(ValueError):Audit(status="COMPLETE",unexpected=True)


def test_decision_credit_unmasked_when_outcomes_fail(seeded):
    with Session(seeded.engine) as db:
        for snap in db.scalars(select(BondMarketSnapshot).where(BondMarketSnapshot.trade_date>=T)):snap.nkd=None
        db.commit()
    result=build(seeded);assert result.status=="COMPLETE",result.blockers
    row=at(result)
    assert row.decision_only_intersection_count==3 and row.decision_plus_outcome_intersection_count==0
    assert row.maximum_credit_cohort_size==3 and row.source_task306a_peer_count==0
    assert "TASK306A_CREDIT_PEER_ZERO_IS_INTERSECTION_DEPENDENT" in row.diagnostics
    assert "AUDIT_LOGIC_FIX" in {r.category for r in result.remediation_categories}


@pytest.mark.parametrize("field,value,gate",[
    ("missing",None,"SECURITY_MASTER_PROFILE_PRESENT"),
    ("currency","USD","RUB_VERIFIED"), ("coupon_structure","floating","FIXED_COUPON"),
    ("perpetual_structure","perpetual","DATED_NON_PERPETUAL"),
    ("frequency",None,"COUPON_FREQUENCY_VERIFIED"),
    ("amortization_structure","amortizing","BULLET_AMORTIZATION"),
    ("maturity",None,"MATURITY_VERIFIED"), ("matured",None,"NOT_MATURED_AT_DATE"),
    ("marker","OFZ-PK","NOT_EXCLUDED_OFZ_PK_IN_AD"),
    ("duration",None,"DURATION_READY"), ("yield",None,"YTM_READY"),
    ("stale",None,"FRESH_HISTORICAL_MOEX_OBSERVATION"),
])
def test_ofz_gate_breakdowns_and_authority(seeded,field,value,gate):
    with Session(seeded.engine) as db:
        bid=seeded.ids[4]
        p=db.scalars(select(BondSecurityMasterProfile).where(BondSecurityMasterProfile.bond_id==bid)).one()
        if field=="missing":db.delete(p)
        elif field=="currency":p.currency_code=value
        elif field=="frequency":p.coupon_frequency_state="unknown";p.coupon_frequency_per_year=None
        elif field=="maturity":p.maturity_state="unknown";p.maturity_date=None
        elif field=="matured":p.maturity_date=T-timedelta(days=1)
        elif field=="marker":db.get(Bond,bid).name=value
        elif field in ("duration","yield","stale"):
            for snap in db.scalars(select(BondMarketSnapshot).where(BondMarketSnapshot.bond_id==bid)):
                if field=="duration":snap.raw_payload={"moex":{"DURATION":"invalid"}}
                elif field=="yield":snap.yield_to_maturity=None
                elif snap.trade_date>=T-timedelta(days=7):db.delete(snap)
        else:setattr(p,field,value)
        db.commit()
    result=build(seeded);assert result.status=="COMPLETE",result.blockers
    row=at(result)
    assert stage(row.ofz_funnel,gate).pass_count==1
    assert any(issue.affected_bond_date_count for issue in row.ofz_reasons)
    if field=="frequency":
        assert row.task306a_ofz_node_count==1 and row.task268_node_count==2 and row.task268_status=="READY"
        assert "TASK306A_OFZ_DIAGNOSTIC_GATE_MISMATCH" in row.diagnostics
    assert all(len(r.representative_bonds)<=10 for r in result.ofz_summary.reasons)


@pytest.mark.parametrize("kind,reason",[
    ("future","PUBLICATION_FUTURE"),("isin","SOURCE_BOND_ISIN_MISMATCH"),
    ("blank","RATING_VALUE_MISSING"),("scale","COHORT_SIZE_BELOW_THREE"),
    ("duplicate","MULTIPLE_LATEST_RATING_EVENTS"),("unresolved","RATING_TARGET_UNRESOLVED"),
])
def test_credit_failures_do_not_depend_on_outcomes(seeded,kind,reason):
    with Session(seeded.engine) as db:
        r=db.scalars(select(CreditRatingEvent).order_by(CreditRatingEvent.id)).first()
        if kind=="future":r.publication_precision="DATE";r.publication_date=T
        elif kind=="isin":r.source_bond_isin="RU000DIFF001"
        elif kind=="blank":r.rating_value_raw=None;r.rating_action_raw="WITHDRAWN"
        elif kind=="scale":r.rating_scale_raw=" "
        elif kind=="unresolved":r.resolution_state="UNRESOLVED";r.bond_id=None
        else:
            duplicate=CreditRatingEvent(artifact_id=r.artifact_id,rating_agency=r.rating_agency,target_kind="BOND",
                resolution_state="RESOLVED",bond_id=r.bond_id,source_bond_isin=r.source_bond_isin,source_object_id="duplicate",
                event_date=r.event_date,publication_precision="UNKNOWN",rating_value_raw="AAA",event_fingerprint="9"*64)
            db.add(duplicate)
        db.commit()
    result=build(seeded);assert result.status=="COMPLETE",result.blockers
    row=at(result)
    assert reason in {r.reason for r in row.credit_reasons}
    assert stage(row.outcome_funnel,"FULL_OBSERVED_OUTCOME").pass_count==5
    assert row.decision_only_intersection_count==0


def test_publication_two_branches_and_issuer_key(seeded):
    with Session(seeded.engine) as db:
        ratings=list(db.scalars(select(CreditRatingEvent).order_by(CreditRatingEvent.id)))
        from app.models.legal_issuer import LegalIssuer
        issuer_id=db.scalars(select(LegalIssuer.id)).one()
        for r in ratings:
            r.target_kind="LEGAL_ISSUER";r.legal_issuer_id=issuer_id;r.bond_id=None
            r.source_bond_isin=None;r.source_issuer_inn="7701234567"
        # One authoritative issuer event supplies the same exact key to three Bonds.
        for r in ratings[1:]:db.delete(r)
        r=ratings[0];r.publication_precision="TIMESTAMP";r.publication_at=datetime.combine(T,datetime.min.time(),timezone.utc)
        db.commit()
    result=build(seeded);assert result.status=="COMPLETE",result.blockers
    row=at(result)
    assert row.credit_cohorts[0].key.target_kind=="LEGAL_ISSUER"
    assert row.credit_proven_cohorts[0].total_member_count==3
    assert "CURRENT_ISSUER_MAPPING_DIAGNOSTIC" in result.known_limitations


def test_horizon_gaps_redemption_and_stage_kinds():
    day=T;intervals=((T,T+timedelta(days=7)),(T+timedelta(days=10),T+timedelta(days=90)))
    assert rca.gap_metrics(day,T+timedelta(days=90),intervals)==(81,8,2,1)
    p={"currency_state":"verified","currency_code":"RUB","nominal_state":"verified","nominal_value":D("1000")}
    index=type("Index",(),{})()
    index.profiles={1:p};index.history={1:[{"price":D("100"),"clean_price":None,"nkd":D("0")}]}
    index.days={1:(T,)};index.intervals={1:((T,T),)}
    index.flows={1:[{"event_date":T+timedelta(days=1),"event_type":"redemption","currency":"RUB","amount":D("1000")}]}
    index.bonds={1:{"isin":"RU000TEST001","secid":"TEST","maturity_date":T+timedelta(days=1)}}
    from collections import defaultdict
    index.reason_ids=defaultdict(set)
    funnel,issues,full,metrics,zero=rca.outcome(day,{1},index,False)
    assert full=={1} and stage(funnel,"FULL_OBSERVED_OUTCOME").pass_count==1
    assert stage(funnel,"FUTURE_HORIZON_IN_DATABASE_RANGE").pass_count==0
    assert stage(funnel,"FUTURE_HORIZON_IN_DATABASE_RANGE").kind=="INDEPENDENT_DIAGNOSTIC"
    assert metrics[0][0]==1 and not zero
    assert not next(r for r in issues if r.reason=="OUTCOME_HORIZON_EXTENDS_BEYOND_MARKET_HISTORY").blocking


def test_readonly_precondition_source_blocked_and_pending_identity_map(seeded,monkeypatch):
    with Session(seeded.engine) as db:
        assert rca.HistoricalReplayBlockerRootCauseAuditService(db).build().blockers==("CONSISTENT_READ_ONLY_TRANSACTION_REQUIRED",)
        db.execute(select(Bond.id)).first()
        assert rca.HistoricalReplayBlockerRootCauseAuditService(db).build().status=="BLOCKED"
    with readonly(seeded.engine) as db:
        expected=old.ModernHistoricalReplayReadinessAuditService(db).build()
        pending=Company(name="Pending",ticker="RCA_PENDING");db.add(pending)
        with db.no_autoflush:
            bond=db.get(Bond,seeded.ids[4]);bond.name="OFZ-PK caller pending"
            profile=db.scalars(select(BondSecurityMasterProfile).where(BondSecurityMasterProfile.bond_id==bond.id)).one()
            profile.coupon_structure="floating"
            deleted=db.get(Company,bond.company_id);db.delete(deleted)
        states=set(db.new),set(db.dirty),set(db.deleted);sql=[];commits=[]
        event.listen(seeded.engine,"before_cursor_execute",lambda c,u,s,p,x,m:sql.append(s))
        event.listen(seeded.engine,"commit",lambda c:commits.append(1))
        result=rca.HistoricalReplayBlockerRootCauseAuditService(db).build()
        assert result.status=="COMPLETE",result.blockers
        assert result.source_readiness_audit_sha256==expected.audit_sha256
        assert at(result).task268_node_count==2
        assert states==(set(db.new),set(db.dirty),set(db.deleted))
        assert db.connection().connection.driver_connection.in_transaction
        assert not commits and all(s.lstrip().upper().startswith(("SELECT","WITH","PRAGMA QUERY_ONLY")) for s in sql)
        assert bond.name=="OFZ-PK caller pending" and profile.coupon_structure=="floating"
    with readonly(seeded.engine) as db:
        monkeypatch.setattr(old.ModernHistoricalReplayReadinessAuditService,"build",lambda self:old.signed(old.Audit(status="BLOCKED",audit_classification="INSUFFICIENT_FOR_REPLAY")))
        monkeypatch.setattr(rca,"read_evidence",lambda db:pytest.fail("supporting reads after BLOCKED"))
        assert rca.HistoricalReplayBlockerRootCauseAuditService(db).build().blockers==("SOURCE_TASK306A_BLOCKED",)


def test_source_sha_tampering_blocked(seeded,monkeypatch):
    original=old.ModernHistoricalReplayReadinessAuditService.build
    monkeypatch.setattr(old.ModernHistoricalReplayReadinessAuditService,"build",lambda self:original(self).model_copy(update={"audit_sha256":"0"*64}))
    assert build(seeded).blockers==("RCA_EVIDENCE_OR_SOURCE_BINDING_INVALID",)


def test_static_no_network_no_financial_or_mutation_calls():
    path=Path(rca.__file__);tree=ast.parse(path.read_text(encoding="utf-8"))
    calls={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
    assert not calls&{"commit","flush","add_all","delete","insert","sync","evaluate_bond","build_for_bond"}
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "update" and isinstance(n.func.value, ast.Name) and n.func.value.id in {"db", "session", "connection"} for n in ast.walk(tree))
    imports=[n.module or "" for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    assert not any(x.startswith(("httpx","requests","app.services.investment_model","app.services.shadow_execution")) for x in imports)
    assert not {"returns","profit","alpha","sharpe","cagr"}&set(Audit.model_fields)


@pytest.mark.parametrize("kind,amount,currency,second,reason",[
    ("coupon",None,"RUB",None,"CASHFLOW_AMOUNT_OR_CURRENCY_INVALID"),
    ("coupon",D("1"),"USD",None,"CASHFLOW_AMOUNT_OR_CURRENCY_INVALID"),
    ("other",D("1"),"RUB",None,"UNSUPPORTED_CASHFLOW_TYPE"),
    ("coupon",D("1"),"RUB","duplicate","DUPLICATE_CASHFLOW_EVENT"),
    ("redemption",D("1000"),"RUB","later_coupon","CASHFLOW_AFTER_REDEMPTION"),
    ("offer_redemption",None,None,None,"OFFER_NOT_AUTOMATIC_CASH"),
])
def test_cashflow_rca_preserves_blockers_and_offer_diagnostic(kind,amount,currency,second,reason):
    from collections import defaultdict
    from types import SimpleNamespace
    flow={"event_date":T+timedelta(days=1),"event_type":kind,"amount":amount,"currency":currency}
    flows=[flow]
    if second=="duplicate":flows.append(dict(flow))
    if second=="later_coupon":flows.append(dict(flow,event_date=T+timedelta(days=2),event_type="coupon"))
    index=SimpleNamespace(profiles={1:{"currency_state":"verified","currency_code":"RUB","nominal_state":"verified","nominal_value":D("1000")}},
        history={1:[{"price":D("100"),"clean_price":None,"nkd":D("0")}]},days={1:(T,)},
        intervals={1:((T,T+timedelta(days=90)),)},flows={1:flows},
        bonds={1:{"isin":"RU000TEST001","secid":"TEST","maturity_date":None}},reason_ids=defaultdict(set))
    funnel,issues,full,metrics,zero=rca.outcome(T,{1},index,True)
    item=next(r for r in issues if r.reason==reason)
    assert item.distinct_bond_count==1 and not zero
    assert item.blocking==(kind!="offer_redemption")
    assert bool(full)==(kind=="offer_redemption")


def test_primary_combination_and_examples_do_not_truncate_counts():
    from collections import defaultdict
    funnel=rca.stages({1,2},{"FIRST":{1},"SECOND":{2}},("FIRST","SECOND"))
    assert rca.primary(funnel)==("COMBINATION_AT_SECOND",)
    assert rca.primary(rca.stages({1,2},{"FIRST":set()},("FIRST",)))==("FIRST",)
    bonds={bid:{"isin":str(bid),"secid":str(bid)} for bid in range(1,13)}
    bank=defaultdict(set)
    result=rca.reasons({"MISSING":set(bonds)},bonds,bank=bank)
    assert result[0].distinct_bond_count==12 and len(result[0].representative_bonds)==10
    assert bank["MISSING"]==set(bonds) and result[0].affected_pct==D("100")
