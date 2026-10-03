"""Offline frozen evidence bridge. PLAN reads; explicitly authorized APPLY owns its transaction."""
from collections.abc import Sequence
from datetime import datetime, timezone
import hashlib
import json

from pydantic import BaseModel
from sqlalchemy import select, text

from app.models.bond import Bond
from app.models.bond_security_master_profile import BondSecurityMasterProfile as Profile
from app.models.bond_security_master_evidence import BondSecurityMasterEvidence as Evidence
from app.schemas.execution_terms_evidence_bridge import (
    ExecutionLotEvidenceV1, ExecutionBoardEvidenceV1, ExecutionTermsAssertion,
    ExecutionTermsBridgeRow, ExecutionTermsEvidenceBridgePlanV1 as Plan,
    ExecutionTermsEvidenceApplyAuthorizationV1 as Authorization,
    ExecutionTermsEvidenceApplyReceipt as Receipt, ExecutionTermsEvidenceAudit as Audit,
)
from app.services.tinvest_frozen_admission_evidence_service import TInvestFrozenAdmissionEvidenceService as Frozen
from app.services.tinvest_bond_import_preflight_service import TInvestBondImportPreflightService as Preflight
from app.services.bond_security_master_service import BondSecurityMasterService as Master


def _json(value):
    if isinstance(value, BaseModel): return _json(value.model_dump())
    if isinstance(value, datetime): return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict): return {k:_json(v) for k,v in value.items()}
    if isinstance(value, (tuple,list)): return [_json(v) for v in value]
    # Numeric DB scalars are not economic calculations; preserve decimal strings.
    from decimal import Decimal
    if isinstance(value, Decimal): return str(value)
    from datetime import date
    if isinstance(value, date): return value.isoformat()
    return value


def _hash(value):
    return hashlib.sha256(json.dumps(_json(value),sort_keys=True,ensure_ascii=True,
        separators=(",",":"),allow_nan=False).encode()).hexdigest()


def _strict(value, kind):
    if type(value) is not kind: raise ValueError("Invalid contract")
    restored=kind.model_validate(value.model_dump())
    def types(v):
        if isinstance(v,BaseModel):
            if hasattr(v,"pit_ready") and v.pit_ready is not False: raise ValueError("Invalid PIT")
            return (type(v).__name__,tuple((k,types(getattr(v,k))) for k in type(v).model_fields))
        if isinstance(v,dict): return ("dict",tuple((k,types(x)) for k,x in sorted(v.items())))
        if isinstance(v,(tuple,list)): return (type(v).__name__,tuple(types(x) for x in v))
        return (type(v).__name__,v)
    if types(value) != types(restored): raise ValueError("Invalid original types")


def _targets(value):
    if not isinstance(value,Sequence) or isinstance(value,(str,bytes,bytearray)): raise ValueError()
    ids=tuple(value)
    if not ids or any(type(i) is not int or i<=0 for i in ids) or len(set(ids))!=len(ids): raise ValueError()
    return tuple(sorted(ids))


def _stamp(value):
    if value is None: return None
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None: raise ValueError()
    return value.astimezone(timezone.utc)


def _inputs(frozen,preflight,ids,board_at):
    try: ids=_targets(ids)
    except Exception: return (),None,("TARGET_IDS_INVALID",)
    try: board_at=_stamp(board_at)
    except Exception: return ids,None,("BOARD_OBSERVED_AT_INVALID",)
    try:
        Frozen.validate(frozen)
        if frozen.acquisition_complete is not True or frozen.ready_for_production_freeze is not True: raise ValueError()
    except Exception: return ids,board_at,("FROZEN_ADMISSION_INVALID",)
    try:
        Preflight.validate_frozen_preflight(preflight,Frozen.replay(frozen))
        p=preflight.provenance
        if (preflight.contract_version != "tinvest-bond-import-preflight-v2" or p.board_scan_status != "COMPLETE" or
            p.board_ready_requires_board_observed is not True or p.board_observation_requested_board != "TQCB"): raise ValueError()
    except Exception: return ids,board_at,("PREFLIGHT_INVALID",)
    return ids,board_at,()


def _read(db,ids):
    with db.no_autoflush:
        bonds=[dict(r) for r in db.execute(select(Bond.id,Bond.isin,Bond.secid).where(Bond.id.in_(ids)).order_by(Bond.id)).mappings()]
        profiles=[dict(r) for r in db.execute(select(*Profile.__table__.columns).where(Profile.bond_id.in_(ids)).order_by(Profile.bond_id)).mappings()]
        evidence=[dict(r) for r in db.execute(select(Evidence.id,Evidence.bond_id,Evidence.field_name,Evidence.source,
            Evidence.source_key,Evidence.source_table,Evidence.assertion_type,Evidence.normalized_value_json,
            Evidence.raw_value_json,Evidence.observed_at,Evidence.effective_at,Evidence.evidence_fingerprint,Evidence.contract_version)
            .where(Evidence.bond_id.in_(ids),Evidence.field_name.in_(("lot_size","trading_board")))
            .order_by(Evidence.bond_id,Evidence.field_name,Evidence.source,Evidence.id)).mappings()]
    return dict(bonds=bonds,profiles=profiles,evidence=evidence)


def _effective(rows):
    latest={}
    for row in rows:
        key=(row["field_name"],row["source"])
        time=Master._persisted_utc_timestamp(row["observed_at"])
        if key not in latest or time>latest[key][0]: latest[key]=(time,[row])
        elif time==latest[key][0]: latest[key][1].append(row)
    return [r for _,rs in latest.values() for r in rs]


def _fingerprint(bond_id,a):
    return Master._fingerprint(bond_id=bond_id,field_name=a.field_name,source=a.source,source_key=a.source_key,
        source_table=a.source_table,assertion_type=a.assertion_type,normalized_value={"value":a.normalized_value},effective_at=None)


def _lineage(bond_id,assertions,rows):
    by_hash={r["evidence_fingerprint"]:r for r in rows}
    for a in assertions:
        r=by_hash.get(_fingerprint(bond_id,a))
        if r is None or (r["normalized_value_json"] != {"value":a.normalized_value} or
            r["raw_value_json"] != a.raw_value or r["contract_version"] != "bond-security-master-v2" or
            (r["field_name"],r["source"],r["source_key"],r["source_table"],r["assertion_type"]) !=
            (a.field_name,a.source,a.source_key,a.source_table,a.assertion_type)): return False
        active=[x for x in _effective(rows) if (x["field_name"],x["source"])==(a.field_name,a.source)]
        if not active or any(x["normalized_value_json"] != {"value":a.normalized_value} for x in active): return False
    return bool(assertions)


def _row(bond_id,bond,profile,evidence,frozen,preflight,board_at):
    blockers=set(); diagnostics=set(); lots=[]; assertions=[]
    isin=bond["isin"] if bond else None; secid=bond["secid"] if bond else None
    if not bond: blockers.add("TARGET_BOND_NOT_FOUND")
    matches=[r for r in preflight.candidate_rows if r.isin==isin and r.secid==secid]
    candidate=matches[0] if len(matches)==1 and isin and secid else None
    if candidate is None: blockers.add("TARGET_IDENTITY_MISMATCH")
    if secid is not None and (secid != secid.strip() or len(secid)>128): blockers.add("SOURCE_KEY_NOT_REPRESENTABLE")
    if profile is None: blockers.add("SECURITY_MASTER_PROFILE_MISSING")
    elif profile["contract_version"] != "bond-security-master-v2": blockers.add("SECURITY_MASTER_PROFILE_INVALID")
    sources={s.uid:s for s in frozen.source_bonds}
    eligible=[]
    if candidate:
        for uid in candidate.source_uids:
            source=sources.get(uid)
            if source is None: blockers.add("SOURCE_UID_MISSING"); continue
            if source.uid != source.uid.strip() or len(source.uid)>128: blockers.add("SOURCE_KEY_NOT_REPRESENTABLE")
            state="VERIFIED"
            if source.isin != isin: state="IDENTITY_MISMATCH"; blockers.add("SOURCE_UID_IDENTITY_MISMATCH")
            elif source.class_code != "TQCB": state="BOARD_BINDING_MISSING"; diagnostics.add("OFF_BOARD_UID_NOT_USED")
            elif source.lot is None: state="MISSING"; diagnostics.add("SOURCE_LOT_NOT_SUPPLIED")
            elif type(source.lot) is not int or not 0<source.lot<=2147483647:
                state="INVALID"; blockers.add("LOT_SIZE_INVALID")
            else: eligible.append(source)
            lots.append(ExecutionLotEvidenceV1(bond_id=bond_id,isin=isin,secid=secid,source_uid=uid,
                source_isin=source.isin,source_class_code=source.class_code,source_lot=source.lot,
                api_trade_available=source.api_trade_available,buy_available=source.buy_available,
                availability_classification=source.availability_classification,evidence_state=state))
        if not any(sources[u].class_code=="TQCB" for u in candidate.source_uids if u in sources): blockers.add("TQCB_SOURCE_UID_MISSING")
    values={s.lot for s in eligible}
    lot=next(iter(values)) if len(values)==1 else None
    if len(values)>1:
        blockers.add("LOT_SIZE_CONFLICT")
        lots=[r.model_copy(update={"evidence_state":"CONFLICT"}) if r.evidence_state=="VERIFIED" else r for r in lots]
    if not values: blockers.add("LOT_SIZE_NOT_AVAILABLE")
    board=candidate.board_evidence if candidate else None
    observed=() if board is None else board.observations
    exact=tuple(o for o in observed if o.secid==secid and o.isin==isin and o.requested_board=="TQCB")
    board_blocks=set()
    if board is None or board.scan_status != "COMPLETE" or board.warning_count or board.requested_board != "TQCB": board_blocks.add("BOARD_SCAN_INCOMPLETE")
    if candidate is None or candidate.primary_board != "TQCB" or candidate.board_observed is not True or not exact: board_blocks.add("BOARD_OBSERVATION_MISSING")
    if any((o.secid==secid or o.isin==isin) and (o.secid!=secid or o.isin!=isin) for o in observed): board_blocks.add("BOARD_IDENTITY_CONFLICT")
    blockers.update(board_blocks)
    if board_at is None: blockers.add("BOARD_OBSERVED_AT_MISSING")
    board_result=ExecutionBoardEvidenceV1(bond_id=bond_id,isin=isin,secid=secid,
        requested_board=board.requested_board if board else None,scan_status=board.scan_status if board else None,
        matched_observation_count=len(exact),matched_observations=exact,board="TQCB" if not board_blocks else None,
        evidence_state="VERIFIED" if not board_blocks else "CONFLICT" if "BOARD_IDENTITY_CONFLICT" in board_blocks else
            "INCOMPLETE" if "BOARD_SCAN_INCOMPLETE" in board_blocks else "MISSING")
    if lot is not None:
        for s in sorted(eligible,key=lambda s:s.uid):
            assertions.append(ExecutionTermsAssertion(field_name="lot_size",source="tinvest_universe",normalized_value=lot,
                source_key=s.uid,source_table=s.contract_version,observed_at=frozen.captured_at,
                raw_value=dict(isin=s.isin,class_code=s.class_code,lot=lot,availability_classification=s.availability_classification)))
    if not board_blocks:
        assertions.append(ExecutionTermsAssertion(field_name="trading_board",source="moex_universe",normalized_value="TQCB",
            source_key=secid,source_table="TQCB",observed_at=board_at,
            raw_value=dict(isin=isin,secid=secid,requested_board="TQCB",scan_status="COMPLETE")))
    for field,wanted,reason in (("lot_size",lot,"CURRENT_LOT_CONFLICT"),("trading_board","TQCB","CURRENT_BOARD_CONFLICT")):
        state_key="lot_size_state" if field=="lot_size" else "trading_board_state"
        if profile:
            state,value=profile[state_key],profile[field]
            valid=(state in ("unknown","verified","conflict") and
                ((state=="verified" and (type(value) is int and 0<value<=2147483647 if field=="lot_size" else type(value) is str and bool(value.strip()))) or
                 (state in ("unknown","conflict") and value is None)))
            if not valid: blockers.add("SECURITY_MASTER_PROFILE_INVALID")
            if state=="conflict" or (state=="verified" and wanted is not None and value!=wanted): blockers.add(reason)
        for e in _effective(evidence):
            if e["field_name"]==field:
                try:
                    raw=e["normalized_value_json"]["value"]
                    canonical=Master._validated_canonical_value(field,e["assertion_type"],raw)
                    if type(raw) is not type(canonical) or raw != canonical: raise ValueError()
                except Exception: blockers.add("SECURITY_MASTER_PROFILE_INVALID")
                if wanted is not None and e["normalized_value_json"] != {"value":wanted}: blockers.add(reason)
    existing={e["evidence_fingerprint"]:e for e in evidence}
    for a in assertions:
        e=existing.get(_fingerprint(bond_id,a))
        if e is not None and (e["raw_value_json"]!=a.raw_value or e["normalized_value_json"]!={"value":a.normalized_value} or
            e["contract_version"]!="bond-security-master-v2" or
            (e["field_name"],e["source"],e["source_key"],e["source_table"],e["assertion_type"])!=
            (a.field_name,a.source,a.source_key,a.source_table,a.assertion_type)):
            blockers.add("CURRENT_LINEAGE_CONFLICT")
    satisfied=profile is not None and profile["lot_size_state"]=="verified" and profile["lot_size"]==lot and profile["trading_board_state"]=="verified" and profile["trading_board"]=="TQCB" and _lineage(bond_id,assertions,evidence)
    return ExecutionTermsBridgeRow(bond_id=bond_id,isin=isin,secid=secid,profile_id=profile["id"] if profile else None,
        lot=lot,lot_evidence=tuple(lots),board_evidence=board_result,assertions=tuple(assertions),
        status="BLOCKED" if blockers else "ALREADY_SATISFIED" if satisfied else "READY_TO_APPLY",
        blockers=tuple(sorted(blockers)),diagnostics=tuple(sorted(diagnostics)))


class ExecutionTermsEvidenceBridgeService:
    def __init__(self,session_factory): self.session_factory=session_factory

    def _fresh(self):
        db=self.session_factory()
        if db.in_transaction() or db.new or db.dirty or db.deleted:
            # A caller-owned/nonfresh session must not be closed or rolled back here.
            raise ValueError("SESSION_NOT_FRESH")
        return db

    def _plan(self,db,frozen,preflight,ids,board_at):
        state=_read(db,ids)
        bonds={b["id"]:b for b in state["bonds"]}; profiles={p["bond_id"]:p for p in state["profiles"]}
        rows=tuple(_row(i,bonds.get(i),profiles.get(i),[e for e in state["evidence"] if e["bond_id"]==i],
            frozen,preflight,board_at) for i in ids)
        identities=[(b["isin"],b["secid"]) for b in state["bonds"]]
        rows=tuple(r.model_copy(update={"status":"BLOCKED","blockers":tuple(sorted(set(r.blockers)|{"TARGET_IDENTITY_MISMATCH"}))})
            if identities.count((r.isin,r.secid))>1 else r for r in rows)
        blockers=tuple(sorted({b for r in rows for b in r.blockers}))
        plan=Plan(status="BLOCKED" if blockers else "EXECUTABLE",target_bond_ids=ids,
            target_bond_id_set_sha256=_hash(ids),frozen_admission_sha256=frozen.artifact_sha256,
            import_preflight_sha256=_hash(preflight),current_db_state_sha256=_hash(state),board_observed_at=board_at,
            rows=rows,ready_count=sum(r.status=="READY_TO_APPLY" for r in rows),
            already_satisfied_count=sum(r.status=="ALREADY_SATISFIED" for r in rows),blocked_count=sum(r.status=="BLOCKED" for r in rows),blockers=blockers)
        return plan.model_copy(update={"plan_sha256":_hash(plan.model_dump(exclude={"plan_sha256"}))})

    def plan(self,*,frozen_admission,preflight,target_bond_ids,board_observed_at=None):
        ids,stamp,errors=_inputs(frozen_admission,preflight,target_bond_ids,board_observed_at)
        if errors: return Plan(status="BLOCKED",target_bond_ids=ids,blockers=errors)
        try: db=self._fresh()
        except ValueError: return Plan(status="BLOCKED",target_bond_ids=ids,blockers=("SESSION_NOT_FRESH",))
        try: return self._plan(db,frozen_admission,preflight,ids,stamp)
        except Exception: return Plan(status="BLOCKED",target_bond_ids=ids,blockers=("LOCK_OR_TRANSACTION_ERROR",))
        finally: db.close()

    def _audit(self,db,plan):
        state=_read(db,plan.target_bond_ids); verified=[]; blockers=set(); lot_count=board_count=0
        bonds={b["id"]:b for b in state["bonds"]}; profiles={p["bond_id"]:p for p in state["profiles"]}
        for row in plan.rows:
            bond=bonds.get(row.bond_id); p=profiles.get(row.bond_id)
            identity=bond is not None and (bond["isin"],bond["secid"])==(row.isin,row.secid)
            lp=p is not None and p["lot_size_state"]=="verified" and p["lot_size"]==row.lot
            bp=p is not None and p["trading_board_state"]=="verified" and p["trading_board"]=="TQCB"
            lot_count+=lp; board_count+=bp
            if identity and lp and bp and p["id"]==row.profile_id and p["contract_version"]=="bond-security-master-v2" and _lineage(row.bond_id,row.assertions,[e for e in state["evidence"] if e["bond_id"]==row.bond_id]): verified.append(row.bond_id)
            else: blockers.add("POST_APPLY_VERIFICATION_FAILED")
        return Audit(status="FAILED" if blockers else "VERIFIED",target_bond_ids=plan.target_bond_ids,
            verified_bond_ids=tuple(verified),lot_verified_count=lot_count,board_verified_count=board_count,
            execution_terms_ready_count=len(verified),current_db_state_sha256=_hash(state),blockers=tuple(sorted(blockers)))

    def apply(self,*,frozen_admission,preflight,reviewed_plan,authorization):
        try:
            _strict(reviewed_plan,Plan); _strict(authorization,Authorization)
            if reviewed_plan.status!="EXECUTABLE" or reviewed_plan.blockers: raise ValueError()
            if _hash(reviewed_plan.model_dump(exclude={"plan_sha256"}))!=reviewed_plan.plan_sha256: return Receipt(status="BLOCKED",blockers=("PLAN_SHA_MISMATCH",))
            for field in ("plan_sha256","target_bond_id_set_sha256","frozen_admission_sha256","import_preflight_sha256","current_db_state_sha256"):
                if getattr(authorization,field)!=getattr(reviewed_plan,field): raise ValueError()
        except Exception: return Receipt(status="BLOCKED",blockers=("AUTHORIZATION_MISMATCH",))
        ids,stamp,errors=_inputs(frozen_admission,preflight,reviewed_plan.target_bond_ids,reviewed_plan.board_observed_at)
        if errors: return Receipt(status="BLOCKED",blockers=errors)
        if (frozen_admission.artifact_sha256!=reviewed_plan.frozen_admission_sha256 or _hash(preflight)!=reviewed_plan.import_preflight_sha256): return Receipt(status="BLOCKED",blockers=("SOURCE_HASH_MISMATCH",))
        base=dict(target_bond_ids=ids,plan_sha256=reviewed_plan.plan_sha256,authorization=authorization,
            authorization_sha256=_hash(authorization),pre_state_sha256=reviewed_plan.current_db_state_sha256)
        try: db=self._fresh()
        except ValueError: return Receipt(**base,status="BLOCKED",blockers=("SESSION_NOT_FRESH",))
        attempted=created=reused=0; committing=False; audit=None
        try:
            dialect=db.get_bind().dialect.name
            if dialect=="sqlite": db.execute(text("BEGIN IMMEDIATE"))
            elif dialect=="postgresql":
                db.begin()
                for table in ("bonds","bond_security_master_evidence","bond_security_master_profiles"):
                    db.execute(text("LOCK TABLE "+table+" IN SHARE ROW EXCLUSIVE MODE NOWAIT"))
            else: raise ValueError("APPLY_DIALECT_UNSUPPORTED")
            _,_,locked_errors=_inputs(frozen_admission,preflight,ids,stamp)
            if locked_errors: raise ValueError(locked_errors[0])
            current=self._plan(db,frozen_admission,preflight,ids,stamp)
            if current!=reviewed_plan: raise ValueError("CURRENT_DB_DRIFT")
            before=_read(db,ids)
            if current.ready_count==0:
                audit=self._audit(db,current)
                if audit.status!="VERIFIED": raise ValueError("POST_APPLY_VERIFICATION_FAILED")
                return Receipt(**base,status="IDEMPOTENT_NOOP",post_state_sha256=audit.current_db_state_sha256,
                    profiles_verified=len(ids),lot_verified_count=audit.lot_verified_count,board_verified_count=audit.board_verified_count,
                    execution_terms_ready_count=audit.execution_terms_ready_count,pre_commit_audit=audit)
            master=Master(db)
            for row in current.rows:
                if row.status=="ALREADY_SATISFIED": continue
                attempted+=1
                bond=db.get(Bond,row.bond_id)
                for a in row.assertions:
                    _,is_new=master.record_assertion(bond=bond,field_name=a.field_name,source=a.source,
                        assertion_type=a.assertion_type,normalized_value=a.normalized_value,observed_at=a.observed_at,
                        source_key=a.source_key,source_table=a.source_table,raw_value=a.raw_value)
                    created+=is_new; reused+=not is_new
                master.resolve_profile(bond)
            audit=self._audit(db,current)
            if audit.status!="VERIFIED": raise ValueError("POST_APPLY_VERIFICATION_FAILED")
            after=_read(db,ids)
            excluded={"lot_size","lot_size_state","trading_board","trading_board_state","last_resolved_at","updated_at"}
            if ([{k:v for k,v in p.items() if k not in excluded} for p in before["profiles"]] !=
                [{k:v for k,v in p.items() if k not in excluded} for p in after["profiles"]]): raise ValueError("POST_APPLY_VERIFICATION_FAILED")
            committing=True; db.commit()
        except Exception as exc:
            rolled_back=False
            try: db.rollback(); rolled_back=True
            except Exception: pass
            unknown=committing or not rolled_back
            safe={"APPLY_DIALECT_UNSUPPORTED","CURRENT_DB_DRIFT","POST_APPLY_VERIFICATION_FAILED",
                "FROZEN_ADMISSION_INVALID","PREFLIGHT_INVALID","TARGET_IDS_INVALID","BOARD_OBSERVED_AT_INVALID"}
            code=str(exc) if type(exc) is ValueError and str(exc) in safe else "COMMIT_ERROR" if committing else "EVIDENCE_OR_RESOLUTION_ERROR" if attempted else "LOCK_OR_TRANSACTION_ERROR"
            return Receipt(**base,status="COMMIT_OUTCOME_UNKNOWN" if unknown else "ROLLED_BACK" if attempted else "BLOCKED",
                attempted_rows=attempted,evidence_created=created,evidence_reused=reused,committed_evidence_created=None if unknown else 0,
                commit_count=None if committing else 0,db_mutated=None if unknown else False,rollback_confirmed=rolled_back and not unknown,
                pre_commit_audit=audit,blockers=(code,))
        finally: db.close()
        result=Receipt(**base,status="APPLIED",attempted_rows=attempted,evidence_created=created,evidence_reused=reused,
            committed_evidence_created=created,commit_count=1,db_mutated=bool(attempted),pre_commit_audit=audit,
            post_state_sha256=audit.current_db_state_sha256,profiles_verified=len(ids),lot_verified_count=audit.lot_verified_count,
            board_verified_count=audit.board_verified_count,execution_terms_ready_count=audit.execution_terms_ready_count)
        try:
            verify=self._fresh()
            try: post=self._audit(verify,reviewed_plan)
            finally: verify.close()
            if post.status!="VERIFIED" or post.current_db_state_sha256!=audit.current_db_state_sha256: raise ValueError()
            return result.model_copy(update={"post_commit_audit":post})
        except Exception: return result.model_copy(update={"status":"POST_COMMIT_AUDIT_FAILED","blockers":("POST_APPLY_VERIFICATION_FAILED",)})
