"""Pure archival normalization; no invented historical availability."""
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
import json
import re
from types import SimpleNamespace
from pydantic import BaseModel
from app.services.historical_audit_canonical_json import chunks, digest
from app.services.moex_iss_client import MoexIssClient
from app.services.moex_market_data_service import map_moex_history_row

VERSION="historical-evidence-mapping-v1"
LIMIT=8*1024*1024
SECRET_KEYS={"authorization","password","passwd","token","accesstoken","refreshtoken","cookie","cookies","headers","credentials","connectionstring","apikey","apitoken","clientsecret","privatekey","bearer"}


def safe_json(value):
    if isinstance(value,BaseModel): return {k:safe_json(getattr(value,k)) for k in type(value).model_fields}
    if isinstance(value,dict):
        if any(type(k) is not str or re.sub(r"[_-]","",k.casefold()) in SECRET_KEYS for k in value): raise ValueError("UNSAFE_SOURCE_FIELDS")
        return {k:safe_json(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)): return [safe_json(v) for v in value]
    if isinstance(value,Decimal):
        if not value.is_finite(): raise ValueError("NONFINITE_SOURCE_VALUE")
        return str(value)
    if isinstance(value,date): return value.isoformat()
    if value is None or type(value) in (str,int,bool): return value
    if type(value) is float:
        number=Decimal(str(value))
        if not number.is_finite(): raise ValueError("NONFINITE_SOURCE_VALUE")
        return str(number)
    raise ValueError("INVALID_SOURCE_VALUE")


def sha(value): return digest(value,exclude=())


def json_bytes(value): return b"".join(p.encode("ascii") for p in chunks(value))


def checked(model,field):
    data={k:getattr(model,k) for k in type(model).model_fields if k!=field}
    if sha(data)!=getattr(model,field): raise ValueError("FROZEN_HASH_MISMATCH")
    # Validate model_copy tampering as well as deserialized objects.
    type(model).model_validate({k:getattr(model,k) for k in type(model).model_fields})
    safe_json(data)
    return model


def signed(cls,field,**values):
    result=cls(**values,**{field:""})
    return result.model_copy(update={field:sha({k:getattr(result,k) for k in cls.model_fields if k!=field})})


def ids(values):
    if not isinstance(values,Sequence) or isinstance(values,(str,bytes)) or not values: raise ValueError("DETERMINISTIC_NONEMPTY_SEQUENCE_REQUIRED")
    if any(type(x) is not int or x<=0 for x in values) or len(set(values))!=len(values): raise ValueError("UNIQUE_POSITIVE_IDS_REQUIRED")
    return tuple(sorted(values))


def text(value): return value if type(value) is str and value.strip() else None


def raw_date(value):
    if type(value) is date: return value
    if type(value) is str:
        try: return date.fromisoformat(value)
        except ValueError: pass
    return None


def value(row,*names):
    matches=[v for k,v in row.items() if k.upper() in names and v is not None and v!=""]
    if len({str(v) for v in matches})>1: raise ValueError("SOURCE_ALIAS_CONFLICT")
    return matches[0] if matches else None


def normalize(query,row):
    raw=safe_json(row)
    secid=text(value(raw,"SECID")) or (query.secid if query.family in ("DESCRIPTION","CASHFLOWS") else None)
    isin=text(value(raw,"ISIN","ISINCODE"))
    # URI/request binding is kept separately from historical issuer proof.
    if isin is None and query.family in ("DESCRIPTION","CASHFLOWS"):isin=query.expected_isin
    if query.family != "REFERENCE":
        if query.secid and secid and secid!=query.secid: raise ValueError("SOURCE_IDENTITY_CONFLICT")
        if query.expected_isin and isin and isin!=query.expected_isin: raise ValueError("SOURCE_IDENTITY_CONFLICT")
    day=None;board=text(value(raw,"BOARDID"));fields={};diagnostics=[]
    proof="UNPROVEN"
    if query.family=="MARKET":
        day=raw_date(value(raw,"TRADEDATE"))
        if not secid or not board or day!=query.trade_date: raise ValueError("SOURCE_HISTORY_BINDING_INVALID")
        normalized=MoexIssClient._normalize_bond_market_history_row(raw)
        mapped,warnings,error=map_moex_history_row(SimpleNamespace(id=1),secid=secid,row=normalized,board=board)
        if error is not None: raise ValueError("SOURCE_MARKET_MAPPING_INVALID")
        fields=safe_json(mapped.model_dump());fields.pop("bond_id");fields.pop("trade_date");fields.pop("source")
        diagnostics=["SOURCE_FIELD_UNAVAILABLE_OR_INVALID"] if warnings else []
        if mapped.nkd is None: diagnostics.append("NKD_SOURCE_EVIDENCE_MISSING")
        # Date proves which trading observation this is, not its historical publication time.
        proof="DATED_SOURCE"
    elif query.family=="LISTING":
        if not secid: raise ValueError("SOURCE_IDENTITY_MISSING")
        start=raw_date(value(raw,"HISTORY_FROM","DATE_FROM","LISTED_FROM","FROM"))
        end=raw_date(value(raw,"HISTORY_TILL","DATE_TILL","LISTED_TILL","TILL"))
        if start and end and end<start: raise ValueError("LISTING_INTERVAL_INVALID")
        fields={"board":board,"start":start.isoformat() if start else None,"end":end.isoformat() if end else None}
        proof="DATED_SOURCE" if start else "UNPROVEN"
    elif query.family=="CASHFLOWS":
        aliases={"coupons":("COUPONDATE","DATE"),"amortizations":("AMORTDATE","DATE"),"redemptions":("REDEMPTIONDATE","MATDATE","DATE"),"offers":("OFFERDATE","DATE")}
        day=raw_date(value(raw,*aliases[query.table]))
        amount=value(raw,"VALUE","AMOUNT");currency=text(value(raw,"FACEUNIT","CURRENCY"))
        try: amount=Decimal(str(amount)) if amount is not None and type(amount) is not bool else None
        except Exception: amount=None
        if amount is not None and (not amount.is_finite() or amount<0): amount=None
        kinds={"coupons":"coupon","amortizations":"amortization","redemptions":"redemption","offers":"offer_redemption"}
        fields={"event_type":kinds[query.table],"amount":str(amount) if amount is not None else None,"amount_percent":None,"currency":currency}
        if not day: diagnostics.append("CASHFLOW_DATE_MISSING")
        if amount is None or currency!="RUB": diagnostics.append("CASHFLOW_AMOUNT_OR_CURRENCY_INVALID")
        diagnostics.append("CONTRACTUAL_HISTORY_COMPLETENESS_UNPROVEN")
        proof="DATED_SOURCE" if day else "UNPROVEN"
    elif query.family in ("DESCRIPTION","REFERENCE"):
        fields=raw;proof="CURRENT_OBSERVATION"
    else: fields=raw
    return {"secid":secid,"isin":isin,"board":board,"event_date":day,"proof_state":proof,
            "values":fields,"diagnostics":tuple(sorted(set(diagnostics))),"mapping_version":VERSION,
            "source_fields":tuple(sorted(raw)),"source_table":query.table,"raw":raw,
            "request_partition":safe_json(query),
            "units":{"price":"percent_of_nominal","nkd":"source_currency_per_unit","duration_years":"years"} if query.family=="MARKET" else {}}


def fits(value,precision,scale):
    if value is None: return True
    try: number=Decimal(str(value))
    except Exception: return False
    if not number.is_finite(): return False
    # Trailing fractional zeroes do not lose numeric value on storage. Inspect
    # digits directly: Decimal.normalize() would depend on the caller's context.
    digits=list(number.as_tuple().digits); exponent=number.as_tuple().exponent
    while digits and digits[-1]==0:
        digits.pop(); exponent+=1
    return (not digits or exponent>=-scale) and (number==0 or number.adjusted()<precision-scale)


def hash_syntax(value):
    if type(value) is not str or not re.fullmatch("[a-f0-9]{64}",value): raise ValueError("INVALID_SHA256")
