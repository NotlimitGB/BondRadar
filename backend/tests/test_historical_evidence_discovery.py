from datetime import date
import pytest
from app.schemas.historical_evidence_foundation import HistoricalAuthorization,HistoricalEvidencePolicy
from app.services.historical_evidence_discovery import HistoricalEvidenceDiscoveryService,read_scope,pages,acquisition_queries
from app.services.historical_evidence_source_client import HistoricalSourceError
from app.services.historical_evidence_normalization import sha
from test_historical_evidence_plans import page,DAY,END,SECID,ISIN


def authorization(policy):
    h=read_scope(policy)
    return HistoricalAuthorization(operation="READ_SOURCE",explicit_authorization=True,plan_sha256=h,source_manifest_sha256=h,current_db_sha256=sha({}),scope_sha256=h)


def test_extinct_security_discovery_all_dates_all_boards_not_current_table():
    calls=[]
    class Client:
        def fetch_page(self,q):
            calls.append(q)
            if q.family=="LISTING":rows=[{"SECID":SECID,"ISIN":ISIN,"BOARDID":"OLD_BOARD","HISTORY_FROM":"2020-01-01","HISTORY_TILL":"2021-01-01"}]
            elif q.family=="MARKET" and q.trade_date==DAY:rows=[{"SECID":"DELISTED","ISIN":"RU000A000002","BOARDID":"TQCB","TRADEDATE":str(DAY),"CLOSE":"90","ACCINT":"1"}]
            else:rows=[]
            return page(q,rows)
    policy=HistoricalEvidencePolicy(cutoff=END);service=HistoricalEvidenceDiscoveryService(Client())
    result=service.discover(policy=policy,authorization=authorization(policy))
    assert result.status=="COMPLETE" and {s.secid for s in result.securities}=={SECID,"DELISTED"}
    assert not result.historical_universe_complete and result.observed_dates==(DAY,)
    assert result==service.discover(policy=policy,authorization=authorization(policy))
    assert {q.trade_date for q in calls if q.family=="MARKET"}=={DAY,END}
    assert all(q.expected_isin for q in acquisition_queries(result) if q.family in ("DESCRIPTION","CASHFLOWS"))


def test_source_absence_and_failure_are_different_and_authorization_precedes_calls():
    class Client:
        def fetch_page(self,q):
            if q.family=="MARKET":raise HistoricalSourceError("SOURCE_REQUEST_REJECTED")
            return page(q,[])
    policy=HistoricalEvidencePolicy(cutoff=DAY)
    result=HistoricalEvidenceDiscoveryService(Client()).discover(policy=policy,authorization=authorization(policy))
    assert result.status=="PARTIAL" and result.blockers==("MARKET:SOURCE_REQUEST_REJECTED",)
    with pytest.raises(ValueError):tuple(acquisition_queries(result))
    with pytest.raises(ValueError):HistoricalEvidenceDiscoveryService(Client()).discover(policy=policy,authorization=authorization(policy).model_copy(update={"scope_sha256":"wrong"}))


@pytest.mark.parametrize("kind",["repeated","overlap","total","missing","exhaustion"])
def test_bad_pagination_never_complete(kind):
    class Client:
        def fetch_page(self,q):
            first=[{"SECID":f"A_{i}"} for i in range(100)]
            second=[{"SECID":f"B_{i}"} for i in range(100)]
            if q.offset==0:return page(q,first,complete=False,next_offset=100,total=200)
            if kind in ("repeated","overlap"):return page(q,first,total=200)
            if kind=="total":return page(q,second,complete=False,next_offset=200,total=201)
            if kind=="missing":return page(q,[{"SECID":"B"}])
            return page(q,second,complete=False,next_offset=200,total=300)
    from app.schemas.historical_evidence_foundation import HistoricalSourceQuery
    with pytest.raises(HistoricalSourceError):tuple(pages(Client(),HistoricalSourceQuery(family="LISTING",table="securities"),max_pages=2))
