"""Explicit, caller-authorized atomic import over frozen Task296C1 evidence.

No source fetching or shared Session: every operation owns a fresh Session.
"""

import hashlib
import json
import re
from collections.abc import Callable
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.company import Company
from app.schemas.controlled_bond_import import (
    BondImportAction, BondImportAuditRow, CompanyImportAction,
    ControlledBondImportAuthorization, ControlledBondImportAudit,
    ControlledBondImportExecutionResult, ControlledBondImportPlan,
    ImportAction, ImportAuditStatus, ImportExecutionStatus, ImportPlanStatus,
    PersistedBondValues,
)
from app.schemas.tinvest_bond_import_preflight import BondImportCompanyPlan
from app.services.tinvest_bond_import_preflight_service import TInvestBondImportPreflightService


def _hash(model):
    data = model.model_dump(mode="json", exclude={"plan_sha256"})
    return hashlib.sha256(json.dumps(data, ensure_ascii=True, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _checked_model(model):
    restored = type(model).model_validate(model.model_dump())
    # Literal booleans can otherwise accept integer 0/1 in constructed models.
    def canonical(value):
        return json.dumps(value.model_dump(mode="json"), sort_keys=True, allow_nan=False)
    if canonical(restored) != canonical(model):
        raise ValueError("ORIGINAL_TYPES_INVALID")


def _name(value):
    return " ".join(value.lower().split())


def _ticker(inn):
    return re.sub(r"[^A-Z0-9_]", "_", f"MOEX_{inn}".upper())[:32]


def _fits(value, precision, scale):
    if type(value) is not Decimal or not value.is_finite():
        return False
    # Tuple arithmetic avoids caller Decimal context and presentation rounding.
    parts = value.as_tuple()
    digits = list(parts.digits)
    exponent = parts.exponent
    while digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    return not digits or (exponent >= -scale and len(digits) + exponent <= precision - scale)


def _payload(row):
    for value, maximum in ((row.isin, 12), (row.secid, 32),
                           (row.name or row.shortname, 255), (row.issuer_name, 255),
                           (row.issuer_inn, 16)):
        if type(value) is not str or not value.strip() or len(value) > maximum:
            raise ValueError("STORAGE_STRING_INVALID")
    if not _fits(row.nominal_value, 14, 2) or row.nominal_value <= 0:
        raise ValueError("STORAGE_NOMINAL_INVALID")
    if row.coupon_rate is not None and (not _fits(row.coupon_rate, 7, 3) or row.coupon_rate < 0):
        raise ValueError("STORAGE_COUPON_INVALID")
    return PersistedBondValues(
        isin=row.isin, secid=row.secid, name=row.name or row.shortname,
        nominal_value=row.nominal_value, maturity_date=row.maturity_date,
        is_perpetual=row.is_perpetual, coupon_rate=row.coupon_rate,
        offer_date=row.offer_date, is_subordinated=row.is_subordinated if row.is_subordinated is not None else False,
        amortization=row.has_amortization,
    )


def _matches(current, values, source):
    fields = values.model_dump()
    # Technical defaults are not authoritative evidence for an existing Bond.
    for key in ("is_floating_coupon", "signal"):
        fields.pop(key)
    if source.is_subordinated is None:
        fields.pop("is_subordinated")
    return all(current[key] == value for key, value in fields.items())


def _validate(preflight, manifest, authorization=None):
    try:
        TInvestBondImportPreflightService.validate_frozen_preflight(preflight, manifest)
        if not preflight.ready_for_import_manifest:
            return "READY_SUBSET_EMPTY"
        if authorization is not None:
            if type(authorization) is not ControlledBondImportAuthorization:
                return "AUTHORIZATION_INVALID"
            if authorization.pit_ready is not False:
                return "AUTHORIZATION_INVALID"
            _checked_model(authorization)
            if (authorization.expected_ready_sha256 != preflight.ready_for_import_manifest_sha256 or
                    authorization.expected_batch_identity != preflight.identity):
                return "AUTHORIZATION_MISMATCH"
    except Exception:
        return "FROZEN_INPUT_INVALID"
    return None


def _read(db, rows):
    isins = {r.isin.strip().upper() for r in rows}
    secids = {r.secid.strip().upper() for r in rows}
    # SQL TRIM varies by dialect and often removes spaces only. The narrow
    # identity projection lets Python apply the exact strip/case diagnostic,
    # including tabs and Unicode whitespace, without auto-matching variants.
    identities = db.execute(select(Bond.id, Bond.isin, Bond.secid).order_by(Bond.id)).all()
    ids = [b.id for b in identities if (b.isin and b.isin.strip().upper() in isins) or
           (b.secid and b.secid.strip().upper() in secids)]
    columns = (Bond.id, Bond.company_id, Bond.isin, Bond.secid, Bond.name,
               Bond.currency, Bond.nominal_value, Bond.maturity_date, Bond.is_perpetual,
               Bond.coupon_rate, Bond.offer_date, Bond.is_subordinated, Bond.amortization,
               Bond.is_floating_coupon, Bond.signal)
    bonds = db.execute(select(*columns).where(Bond.id.in_(ids)).order_by(Bond.id)).mappings().all()
    companies = db.execute(select(Company.id, Company.inn, Company.name, Company.ticker)
                           .order_by(Company.id)).mappings().all()
    return bonds, companies


class ControlledBondImportService:
    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory

    def _fresh(self):
        db = self.session_factory()
        if not isinstance(db, Session) or db.in_transaction() or db.new or db.dirty or db.deleted:
            # A non-fresh session may be caller-owned: do not close or roll it back.
            raise ValueError("SESSION_NOT_FRESH")
        return db

    def _plan(self, db, preflight, authorization):
        rows = preflight.ready_for_import_manifest
        blockers = set()
        if authorization is None:
            blockers.add("AUTHORIZATION_REQUIRED")
        with db.no_autoflush:
            bonds, companies = _read(db, rows)
        by_company = {c["id"]: c for c in companies}
        groups = {}
        actions = []
        for row in rows:
            diagnostics = {"MODEL_DEFAULT_NOT_EVIDENCE"}
            unknown = ["is_floating_coupon"]
            if row.is_subordinated is None:
                unknown.append("is_subordinated")
            if row.has_amortization is None:
                unknown.append("amortization")
            values = None
            try:
                values = _payload(row)
            except ValueError as exc:
                diagnostics.add(str(exc))
                blockers.add(str(exc))
            exact_i = [b for b in bonds if b["isin"] == row.isin]
            exact_s = [b for b in bonds if b["secid"] == row.secid]
            variants = [b for b in bonds if (
                (b["isin"] and b["isin"].strip().upper() == row.isin.strip().upper() and b["isin"] != row.isin) or
                (b["secid"] and b["secid"].strip().upper() == row.secid.strip().upper() and b["secid"] != row.secid))]
            action, bond_id, company_id = ImportAction.CREATE, None, None
            if variants:
                diagnostics.add("NORMALIZED_IDENTIFIER_COLLISION")
            if exact_i or exact_s:
                if len(exact_i) != 1 or len(exact_s) != 1 or exact_i[0]["id"] != exact_s[0]["id"]:
                    diagnostics.add("BOND_IDENTITY_CONFLICT" if exact_i and exact_s else "BOND_PARTIAL_COLLISION")
                else:
                    current = exact_i[0]
                    company = by_company.get(current["company_id"])
                    if (company is None or company["inn"] is None or company["inn"].strip() != row.issuer_inn or
                            (row.company_id is not None and row.company_id != current["company_id"]) or
                            values is None or not _matches(current, values, row)):
                        diagnostics.add("BOND_PROPERTIES_MISMATCH")
                    else:
                        action = ImportAction.ALREADY_PRESENT_EXACT
                        bond_id, company_id = current["id"], current["company_id"]
            failures = diagnostics - {"MODEL_DEFAULT_NOT_EVIDENCE"}
            if failures:
                blockers.update(failures)
                action = ImportAction.BLOCKED
            group = groups.setdefault(row.issuer_inn, {"rows": [], "ids": set(), "create": False})
            group["rows"].append(row)
            if company_id is not None:
                group["ids"].add(company_id)
            if action is ImportAction.CREATE:
                group["create"] = True
            actions.append(BondImportAction(isin=row.isin, secid=row.secid, issuer_inn=row.issuer_inn,
                action=action, bond_id=bond_id, company_id=company_id, values=values,
                unknown_source_fields=tuple(sorted(unknown)), diagnostics=tuple(sorted(diagnostics))))
        company_actions = []
        for inn, group in sorted(groups.items()):
            row = group["rows"][0]
            if len({_name(r.issuer_name) for r in group["rows"]}) != 1:
                blockers.add("ISSUER_NAME_CONFLICT")
            matching = [c for c in companies if c["inn"] and c["inn"].strip() == inn]
            if len(matching) > 1:
                blockers.add("COMPANY_IDENTITY_CONFLICT")
            names = [c for c in companies if _name(c["name"]) == _name(row.issuer_name)]
            ticker = _ticker(inn)
            company_id = min(group["ids"], default=None)
            company_action = ImportAction.ALREADY_PRESENT_EXACT
            if len(group["ids"]) > 1:
                blockers.add("COMPANY_IDENTITY_CONFLICT")
            if group["create"]:
                previews = {(r.planned_company_action, r.company_id) for r in group["rows"]}
                if len(previews) != 1:
                    blockers.add("COMPANY_PREVIEW_CONFLICT")
                if row.planned_company_action is BondImportCompanyPlan.CREATE_NEW:
                    if matching or names or any(c["ticker"] == ticker for c in companies):
                        blockers.add("COMPANY_CREATE_DRIFT")
                    company_action, company_id = ImportAction.CREATE, None
                else:
                    if len(matching) != 1 or matching[0]["id"] != row.company_id:
                        blockers.add("COMPANY_REUSE_DRIFT")
                    else:
                        company_id = matching[0]["id"]
            if company_id is not None:
                ticker = by_company[company_id]["ticker"]
            company_actions.append(CompanyImportAction(inn=inn, name=row.issuer_name, ticker=ticker,
                action=company_action, company_id=company_id))
        plan = ControlledBondImportPlan(
            status=ImportPlanStatus.NOT_EXECUTABLE if blockers else ImportPlanStatus.EXECUTABLE,
            ready_sha256=preflight.ready_for_import_manifest_sha256, batch_identity=preflight.identity,
            authorization=authorization, companies=tuple(company_actions), bonds=tuple(actions),
            planned_company_creates=sum(c.action is ImportAction.CREATE for c in company_actions),
            planned_bond_creates=sum(b.action is ImportAction.CREATE for b in actions),
            already_present_bonds=sum(b.action is ImportAction.ALREADY_PRESENT_EXACT for b in actions),
            blockers=tuple(sorted(blockers)), diagnostics=("MODEL_DEFAULT_NOT_EVIDENCE",),
        )
        return plan.model_copy(update={"plan_sha256": _hash(plan)})

    def plan(self, *, preflight, admission_manifest, authorization=None):
        error = _validate(preflight, admission_manifest, authorization)
        if error:
            return ControlledBondImportPlan(blockers=(error,))
        try:
            db = self._fresh()
        except ValueError:
            return ControlledBondImportPlan(blockers=("SESSION_NOT_FRESH",))
        try:
            return self._plan(db, preflight, authorization)
        finally:
            db.close()

    def _audit(self, db, preflight, receipt=None, created_ids=()):
        with db.no_autoflush:
            bonds, companies = _read(db, preflight.ready_for_import_manifest)
        by_company = {c["id"]: c for c in companies}
        result = []
        for row in preflight.ready_for_import_manifest:
            reasons = set()
            ii = [b for b in bonds if b["isin"] == row.isin]
            ss = [b for b in bonds if b["secid"] == row.secid]
            current = None
            if any((b["isin"] and b["isin"].strip().upper() == row.isin.strip().upper() and b["isin"] != row.isin) or
                   (b["secid"] and b["secid"].strip().upper() == row.secid.strip().upper() and b["secid"] != row.secid)
                   for b in bonds):
                reasons.add("NORMALIZED_IDENTIFIER_COLLISION")
            if not ii or not ss:
                reasons.add("BOND_MISSING")
            elif len(ii) != 1 or len(ss) != 1 or ii[0]["id"] != ss[0]["id"]:
                reasons.add("BOND_IDENTITY_CONFLICT")
            else:
                current = ii[0]
                company = by_company.get(current["company_id"])
                if not company or not company["inn"] or company["inn"].strip() != row.issuer_inn:
                    reasons.add("COMPANY_RELATION_MISMATCH")
                if len([c for c in companies if c["inn"] and c["inn"].strip() == row.issuer_inn]) != 1:
                    reasons.add("COMPANY_IDENTITY_CONFLICT")
                if row.company_id is not None and current["company_id"] != row.company_id:
                    reasons.add("COMPANY_RELATION_MISMATCH")
                try:
                    if not _matches(current, _payload(row), row):
                        reasons.add("BOND_PROPERTIES_MISMATCH")
                    if current["id"] in created_ids or (receipt and current["id"] in receipt.created_bond_ids):
                        if any(current[k] != v for k, v in _payload(row).model_dump().items()):
                            reasons.add("TECHNICAL_DEFAULT_MISMATCH")
                except ValueError:
                    reasons.add("STORAGE_INVALID")
            result.append(BondImportAuditRow(isin=row.isin, secid=row.secid,
                bond_id=current["id"] if current else None, company_id=current["company_id"] if current else None,
                verified=not reasons, diagnostics=tuple(sorted(reasons))))
        return ControlledBondImportAudit(
            status=ImportAuditStatus.VERIFIED if all(r.verified for r in result) else ImportAuditStatus.FAILED,
            ready_sha256=preflight.ready_for_import_manifest_sha256, rows=tuple(result),
            expected_bond_count=len(result), verified_bond_count=sum(r.verified for r in result),
            committed_company_creates=receipt.committed_company_creates if receipt else None,
            committed_bond_creates=receipt.committed_bond_creates if receipt else None,
            reused_company_count=receipt.reused_company_count if receipt else None,
            already_present_bond_count=receipt.already_present_bond_count if receipt else None,
            diagnostics=("HISTORICAL_COUNTS_FROM_RECEIPT" if receipt else "HISTORICAL_COUNTS_NOT_OBSERVABLE",
                         "GLOBAL_UNRELATED_MUTATIONS_NOT_OBSERVABLE", "MODEL_DEFAULT_NOT_EVIDENCE"),
        )

    def audit(self, *, preflight, admission_manifest, execution_result=None):
        error = _validate(preflight, admission_manifest)
        if execution_result is not None:
            try:
                if type(execution_result) is not ControlledBondImportExecutionResult:
                    raise ValueError()
                _checked_model(execution_result)
                if (execution_result.pit_ready is not False or execution_result.capabilities.pit_ready is not False or
                        execution_result.ready_sha256 != preflight.ready_for_import_manifest_sha256 or
                        execution_result.batch_identity != preflight.identity):
                    raise ValueError()
            except Exception:
                error = "EXECUTION_RECEIPT_INVALID"
        if error:
            return ControlledBondImportAudit(status=ImportAuditStatus.BLOCKED, diagnostics=(error,))
        try:
            db = self._fresh()
        except ValueError:
            return ControlledBondImportAudit(status=ImportAuditStatus.BLOCKED, diagnostics=("SESSION_NOT_FRESH",))
        try:
            return self._audit(db, preflight, execution_result)
        finally:
            db.close()

    def apply(self, *, preflight, admission_manifest, authorization, reviewed_plan,
              expected_plan_sha256, confirm_apply=False):
        error = _validate(preflight, admission_manifest, authorization)
        if authorization is None:
            error = "AUTHORIZATION_REQUIRED"
        if confirm_apply is not True:
            error = "EXPLICIT_APPLY_CONFIRMATION_REQUIRED"
        try:
            if type(reviewed_plan) is not ControlledBondImportPlan:
                raise ValueError()
            if reviewed_plan.pit_ready is not False or reviewed_plan.capabilities.pit_ready is not False:
                raise ValueError()
            _checked_model(reviewed_plan)
            if (type(expected_plan_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", expected_plan_sha256) or
                    reviewed_plan.plan_sha256 != expected_plan_sha256 or _hash(reviewed_plan) != expected_plan_sha256 or
                    reviewed_plan.status is not ImportPlanStatus.EXECUTABLE or reviewed_plan.authorization != authorization):
                raise ValueError()
        except Exception:
            error = error or "REVIEWED_PLAN_INVALID"
        if error:
            return ControlledBondImportExecutionResult(status=ImportExecutionStatus.BLOCKED, diagnostics=(error,))
        try:
            db = self._fresh()
        except ValueError:
            return ControlledBondImportExecutionResult(status=ImportExecutionStatus.BLOCKED, diagnostics=("SESSION_NOT_FRESH",))
        attempted_c = attempted_b = 0
        created_c, created_b = [], []
        pre_audit = None
        committing = False
        base = dict(ready_sha256=preflight.ready_for_import_manifest_sha256,
                    plan_sha256=expected_plan_sha256, batch_identity=preflight.identity,
                    planned_company_creates=reviewed_plan.planned_company_creates,
                    planned_bond_creates=reviewed_plan.planned_bond_creates,
                    already_present_bond_count=reviewed_plan.already_present_bonds,
                    reused_company_count=sum(c.action is ImportAction.ALREADY_PRESENT_EXACT for c in reviewed_plan.companies))
        try:
            dialect = db.get_bind().dialect.name
            if dialect == "sqlite":
                db.execute(text("BEGIN IMMEDIATE"))
            elif dialect == "postgresql":
                db.begin()
                db.execute(text("LOCK TABLE companies IN SHARE ROW EXCLUSIVE MODE NOWAIT"))
                db.execute(text("LOCK TABLE bonds IN SHARE ROW EXCLUSIVE MODE NOWAIT"))
            else:
                raise ValueError("APPLY_DIALECT_UNSUPPORTED")
            current = self._plan(db, preflight, authorization)
            if current != reviewed_plan or current.plan_sha256 != expected_plan_sha256:
                raise ValueError("CURRENT_DB_PLAN_DRIFT")
            ids = {}
            expected_new = set()
            def flush_expected():
                if db.dirty or db.deleted or not set(db.new).issubset(expected_new):
                    raise ValueError("UNEXPECTED_PENDING_MUTATION")
                db.flush()
            for company in current.companies:
                if company.action is ImportAction.CREATE:
                    attempted_c += 1
                    obj = Company(name=company.name, inn=company.inn, ticker=company.ticker,
                                  country=company.country, signal=company.signal)
                    db.add(obj)
                    expected_new.add(obj)
                    flush_expected()
                    created_c.append(obj.id)
                    ids[company.inn] = obj.id
                else:
                    ids[company.inn] = company.company_id
            for bond in current.bonds:
                if bond.action is ImportAction.CREATE:
                    attempted_b += 1
                    obj = Bond(company_id=ids[bond.issuer_inn], **bond.values.model_dump())
                    db.add(obj)
                    expected_new.add(obj)
                    flush_expected()
                    created_b.append(obj.id)
            if db.new or db.dirty or db.deleted:
                raise ValueError("UNEXPECTED_PENDING_MUTATION")
            pre_audit = self._audit(db, preflight, created_ids=created_b)
            if pre_audit.status is not ImportAuditStatus.VERIFIED:
                raise ValueError("PRE_COMMIT_AUDIT_FAILED")
            committing = True
            db.commit()
        except Exception as exc:
            # No raw DB exceptions or values enter the public contract.
            rollback = False
            try:
                db.rollback()
                rollback = True
            except Exception:
                pass
            unknown = committing or not rollback
            safe_codes = {"CURRENT_DB_PLAN_DRIFT", "APPLY_DIALECT_UNSUPPORTED", "PRE_COMMIT_AUDIT_FAILED", "UNEXPECTED_PENDING_MUTATION"}
            code = str(exc) if type(exc) is ValueError and str(exc) in safe_codes else "COMMIT_ERROR" if committing else "LOCK_OR_TRANSACTION_ERROR" if not (attempted_c or attempted_b) else "INSERT_OR_AUDIT_ERROR"
            return ControlledBondImportExecutionResult(**base,
                status=ImportExecutionStatus.COMMIT_OUTCOME_UNKNOWN if unknown else ImportExecutionStatus.ROLLED_BACK if (attempted_c or attempted_b) else ImportExecutionStatus.BLOCKED,
                attempted_company_creates=attempted_c, attempted_bond_creates=attempted_b,
                committed_company_creates=None if unknown else 0, committed_bond_creates=None if unknown else 0,
                db_mutated=None if unknown else False, commit_count=None if committing else 0,
                rollback_confirmed=rollback and not unknown, pre_commit_audit=pre_audit, diagnostics=(code,))
        finally:
            db.close()
        receipt = ControlledBondImportExecutionResult(**base, status=ImportExecutionStatus.APPLIED,
            attempted_company_creates=attempted_c, attempted_bond_creates=attempted_b,
            committed_company_creates=len(created_c), committed_bond_creates=len(created_b),
            created_company_ids=tuple(created_c), created_bond_ids=tuple(created_b),
            db_mutated=bool(created_c or created_b), commit_count=1, pre_commit_audit=pre_audit)
        try:
            post = self.audit(preflight=preflight, admission_manifest=admission_manifest, execution_result=receipt)
        except Exception:
            post = ControlledBondImportAudit(status=ImportAuditStatus.FAILED, diagnostics=("POST_COMMIT_AUDIT_ERROR",))
        return receipt.model_copy(update={"post_commit_audit": post,
            "status": ImportExecutionStatus.APPLIED if post.status is ImportAuditStatus.VERIFIED else ImportExecutionStatus.APPLIED_AUDIT_FAILED})
