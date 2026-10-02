"""Narrow verified source terms and SELECT-only isolated database loading."""

import ast
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import event

from app.models.bond import Bond
from app.models.company import Company
from app.models.bond_security_master_profile import BondSecurityMasterProfile
from app.schemas.portfolio_strategy import PortfolioStrategyRequest
from app.services.portfolio_strategy_builder import PortfolioStrategyBuilder
from app.services.risk_candidate_reducer import RiskCandidateReducer
from app.services.shadow_execution_terms_loader import ShadowExecutionTermsLoader
from app.services.shadow_execution_planner import make_terms, make_terms_batch, validate_strategy
from test_portfolio_strategy_builder import source

D=Decimal


def strategy(n=3,overrides=None,capital="100000"):
    changes={i:{"security_master_profile_id":400+i,**(overrides or {}).get(i,{})} for i in range(1,n+1)}
    investment=source(n,changes)
    return PortfolioStrategyBuilder.build(investment,RiskCandidateReducer.build(investment),PortfolioStrategyRequest(capital_rub=D(capital)))


def row(p,**changes):
    result=dict(id=p.source_evaluation.candidate.provenance.security_master_profile_id,bond_id=p.bond_id,
        contract_version="bond-security-master-v2",currency_state="verified",currency_code="RUB",
        nominal_state="verified",nominal_value=p.source_evaluation.candidate.features.nominal_value,
        lot_size_state="verified",lot_size=1,trading_board_state="verified",trading_board="TQCB")
    result.update(changes)
    return result


def terms(s,changes=None):
    return make_terms_batch(s,tuple(make_terms(p,None if (changes or {}).get(p.bond_id) is None and p.bond_id in (changes or {})
        else row(p,**(changes or {}).get(p.bond_id,{}))) for p in s.positions))


@pytest.fixture(scope="module")
def selected(): return strategy()


def seed_profiles(db,s):
    company=Company(name="Task302 isolated issuer",ticker="TASK302")
    db.add(company); db.flush()
    for p in s.positions:
        db.add(Bond(id=p.bond_id,company_id=company.id,name="Legacy ignored",isin=f"RU302{p.bond_id:07d}",
            nominal_value=D(9999),currency="USD",current_price=D(777)))
    db.flush()
    for p in s.positions:
        db.add(BondSecurityMasterProfile(**row(p)))
    db.commit()
    return company


@pytest.mark.parametrize("changes,blockers",[
    ({},()),({"id":999},("SECURITY_MASTER_PROFILE_ID_MISMATCH","SECURITY_MASTER_PROFILE_DRIFT")),
    ({"contract_version":"old"},("SECURITY_MASTER_CONTRACT_MISMATCH","SECURITY_MASTER_PROFILE_DRIFT")),
    ({"currency_state":"unknown","currency_code":None},("CURRENCY_NOT_VERIFIED",)),
    ({"currency_state":"conflict","currency_code":None},("CURRENCY_NOT_VERIFIED",)),
    ({"currency_code":"USD"},("CURRENCY_MISMATCH","SECURITY_MASTER_PROFILE_DRIFT")),
    ({"nominal_state":"unknown","nominal_value":None},("NOMINAL_NOT_VERIFIED",)),
    ({"nominal_value":D(2000)},("NOMINAL_MISMATCH","SECURITY_MASTER_PROFILE_DRIFT")),
    ({"lot_size_state":"unknown","lot_size":None},("LOT_SIZE_NOT_VERIFIED",)),
    ({"lot_size_state":"conflict","lot_size":None},("LOT_SIZE_NOT_VERIFIED",)),
    ({"trading_board_state":"unknown","trading_board":None},("TRADING_BOARD_NOT_VERIFIED",)),
    ({"trading_board_state":"conflict","trading_board":None},("TRADING_BOARD_NOT_VERIFIED",)),
    ({"trading_board":" OTHER_BOARD "},()),
])
def test_narrow_terms_gates(selected,changes,blockers):
    result=make_terms(selected.positions[0],row(selected.positions[0],**changes))
    assert result.blockers==tuple(sorted(blockers))
    assert result.status==("UNAVAILABLE" if blockers else "READY")
    if "trading_board" in changes: assert result.trading_board==changes["trading_board"]


@pytest.mark.parametrize("changes",[{"lot_size":0},{"lot_size":True},{"lot_size":1.0},{"lot_size":None},
    {"nominal_value":D("NaN")},{"nominal_value":D("Infinity")},{"nominal_value":D(-1)},
    {"trading_board":" "},{"trading_board":True},{"currency_code":None},
    {"lot_size_state":"unknown","lot_size":1},{"id":True},{"bond_id":999},{"nominal_state":"bad"}])
def test_malformed_projection_is_not_unavailability(selected,changes):
    with pytest.raises(ValueError): make_terms(selected.positions[0],row(selected.positions[0],**changes))


def test_missing_profile_and_simultaneous_blockers(selected):
    p=selected.positions[0]
    result=make_terms(p)
    assert result.blockers==("SECURITY_MASTER_PROFILE_MISSING",) and result.loaded_security_master_profile_id is None
    result=make_terms(p,row(p,id=999,currency_code="USD",nominal_value=D(2000),lot_size_state="conflict",lot_size=None))
    assert result.blockers==tuple(sorted(set(result.blockers))) and len(result.blockers)==5


def test_one_select_projection_order_pending_state_and_immutability(db_session,selected,monkeypatch):
    db=db_session; company=seed_profiles(db,selected)
    deleted=Company(name="Deleted",ticker="TASK302_DELETE"); db.add(deleted); db.commit()
    company.name="Caller dirty"; db.delete(deleted)
    pending=Company(name="Pending",ticker="TASK302_PENDING"); db.add(pending); db.autoflush=True
    state=(set(db.new),set(db.dirty),set(db.deleted)); before=selected.model_dump()
    sql=[]
    def capture(conn,cursor,statement,params,context,many): sql.append(statement)
    def forbidden(*a,**k): pytest.fail("Terms loader mutation")
    for name in ("add","add_all","delete","flush","commit","rollback"): monkeypatch.setattr(db,name,forbidden)
    event.listen(db.bind,"before_cursor_execute",capture)
    try: result=ShadowExecutionTermsLoader(db).build(selected)
    finally: event.remove(db.bind,"before_cursor_execute",capture)
    assert len(sql)==1 and sql[0].lstrip().upper().startswith("SELECT") and " IN " in sql[0]
    assert "bonds." not in sql[0] and "lot_size" in sql[0] and "outstanding_nominal" not in sql[0]
    assert result==terms(selected) and result.strategy_selected_bond_ids==tuple(p.bond_id for p in selected.positions)
    assert selected.model_dump()==before and state==(set(db.new),set(db.dirty),set(db.deleted)) and pending.id is None
    assert db.autoflush is True


def test_database_missing_and_empty_zero_queries(db_session,selected):
    result=ShadowExecutionTermsLoader(db_session).build(selected)
    assert result.unavailable_count==3 and all(t.blockers==("SECURITY_MASTER_PROFILE_MISSING",) for t in result.terms)
    empty=strategy(0); validate_strategy(empty)
    sql=[]
    def capture(conn,cursor,statement,params,context,many): sql.append(statement)
    event.listen(db_session.bind,"before_cursor_execute",capture)
    try: result=ShadowExecutionTermsLoader(db_session).build(empty)
    finally: event.remove(db_session.bind,"before_cursor_execute",capture)
    assert not sql and result.selected_count==0 and result.terms==()


def test_loader_ast_narrow_only():
    tree=ast.parse((Path(__file__).parents[1]/"app/services/shadow_execution_terms_loader.py").read_text())
    imports=[n.module or "" for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    assert all(i.startswith(("sqlalchemy","app.models.bond_security_master_profile","app.services.shadow_execution_planner")) for i in imports)
    forbidden={"add","add_all","flush","commit","rollback","delete","sync","request"}
    assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in forbidden for n in ast.walk(tree))
