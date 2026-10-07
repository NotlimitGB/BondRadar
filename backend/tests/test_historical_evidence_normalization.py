from datetime import date
from decimal import Decimal
from types import SimpleNamespace
import pytest
from app.schemas.historical_evidence_foundation import HistoricalSourceQuery
from app.services.historical_evidence_normalization import normalize,safe_json
from app.services.moex_iss_client import MoexIssClient
from app.services.moex_market_data_service import map_moex_history_row,MoexMarketDataService


@pytest.mark.parametrize("aliases,expected",[
    ({"ACCINT":"0"},"0"),({"ACCRUEDINT":"2"},"2"),({"ACCINT":"2","ACCRUEDINT":2},"2"),
    ({"ACCINT":"2","ACCRUEDINT":"3"},None),({"ACCINT":True},None),({"ACCINT":"NaN"},None),({"ACCINT":"-1"},None),({},None),
])
def test_mapping_parity_nkd_price_raw_and_no_mutation(aliases,expected):
    day=date(2020,9,1);raw={"SECID":"OLD","BOARDID":"TQCB","TRADEDATE":str(day),"CLOSE":"100","MARKETPRICE":"99","DURATION":"365","YIELD":"10",**aliases}
    before=raw.copy();q=HistoricalSourceQuery(family="MARKET",table="history",trade_date=day)
    result=normalize(q,raw);assert raw==before
    assert result["values"]["price"]=="100" and result["values"]["nkd"]==expected
    normalized=MoexIssClient._normalize_bond_market_history_row(raw)
    pure=map_moex_history_row(SimpleNamespace(id=1),secid="OLD",row=normalized,board="TQCB")
    old=MoexMarketDataService.__new__(MoexMarketDataService)._map_history_row(SimpleNamespace(id=1),secid="OLD",row=normalized,board="TQCB",source="moex")
    assert pure==old and result["values"]["raw_payload"]["moex"]==raw


@pytest.mark.parametrize("replace",[{"SECID":None},{"BOARDID":None},{"TRADEDATE":"2020-09-02"}])
def test_exact_binding_required(replace):
    q=HistoricalSourceQuery(family="MARKET",table="history",trade_date=date(2020,9,1))
    with pytest.raises(ValueError):normalize(q,{"SECID":"OLD","BOARDID":"TQCB","TRADEDATE":"2020-09-01",**replace})


def test_current_description_and_cashflow_proof_no_inference():
    q=HistoricalSourceQuery(family="DESCRIPTION",table="description",secid="OLD",expected_isin="RU000A000001")
    row=normalize(q,{"name":"COUPONPERIOD","value":"182"})
    assert row["proof_state"]=="CURRENT_OBSERVATION" and "coupon_frequency" not in str(row)
    q=HistoricalSourceQuery(family="CASHFLOWS",table="coupons",secid="OLD",expected_isin="RU000A000001")
    row=normalize(q,{"COUPONDATE":"2021-01-01","VALUE":None,"FACEUNIT":"RUB"})
    assert row["values"]["amount"] is None and "CONTRACTUAL_HISTORY_COMPLETENESS_UNPROVEN" in row["diagnostics"]
    assert row["values"].get("nominal") is None
    with pytest.raises(ValueError):safe_json({"Authorization":"SECRET"})
    for field in ("api_key","accessToken","PRIVATE-KEY","client_secret"):
        with pytest.raises(ValueError,match="UNSAFE_SOURCE_FIELDS"):safe_json({field:"SECRET"})
