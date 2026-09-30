"""Six isolated critical paths; no live sources or shared database."""

import ast
import hashlib
import json
from decimal import Decimal, getcontext
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.bond import Bond
from app.models.company import Company
from app.schemas.controlled_bond_import import (
    ControlledBondImportAuthorization, ImportAction, ImportAuditStatus,
    ImportExecutionStatus, ImportPlanStatus,
)
from app.services.controlled_bond_import_service import ControlledBondImportService
from app.services.tinvest_bond_import_preflight_service import TInvestBondImportPreflightService
from test_tinvest_bond_import_preflight import source, manifest, description, _run_preflight


def frozen(*, count=2, overrides=None, companies=()):
    admission = manifest([source(f"UID-{i}", f"RU000A100A{i:02d}") for i in range(count)])
    projections = [description(c, **(overrides or {})) for c in admission.import_candidate_manifest]
    preflight = _run_preflight(admission_manifest=admission, moex_descriptions=projections,
                              internal_bonds=[], company_projections=list(companies))
    return admission, preflight


def auth(preflight):
    return ControlledBondImportAuthorization(expected_ready_sha256=preflight.ready_for_import_manifest_sha256,
                                            expected_batch_identity=preflight.identity)


def setup(db_session, cls=Session):
    factory = sessionmaker(bind=db_session.get_bind(), class_=cls, autoflush=True)
    return factory, ControlledBondImportService(factory)


def counts(factory):
    with factory() as db:
        return tuple(db.scalars(select(model.id).order_by(model.id)).all() for model in (Company, Bond))


def apply(service, preflight, admission, plan, **kwargs):
    return service.apply(preflight=preflight, admission_manifest=admission,
                         authorization=auth(preflight), reviewed_plan=plan,
                         expected_plan_sha256=plan.plan_sha256, confirm_apply=True, **kwargs)


def test_authorized_plan_is_deterministic_read_only_and_explicit(db_session):
    factory, service = setup(db_session)
    admission, preflight = frozen(overrides={"is_subordinated": None})
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(db_session.get_bind(), "before_cursor_execute", capture)
    original = preflight.model_dump_json()
    context = getcontext().copy()
    try:
        p1 = service.plan(preflight=preflight, admission_manifest=admission, authorization=auth(preflight))
        p2 = service.plan(preflight=preflight, admission_manifest=admission, authorization=auth(preflight))
        assert p1.status is ImportPlanStatus.EXECUTABLE
        assert p1.model_dump() == p2.model_dump()
        assert p1.planned_company_creates == 1 and p1.planned_bond_creates == 2
        assert tuple((r.isin, r.secid) for r in p1.bonds) == tuple(sorted((r.isin, r.secid) for r in p1.bonds))
        assert all("is_subordinated" in r.unknown_source_fields for r in p1.bonds)
        assert p1.diagnostics == ("MODEL_DEFAULT_NOT_EVIDENCE",)
        assert service.plan(preflight=preflight, admission_manifest=admission).status is ImportPlanStatus.NOT_EXECUTABLE
        assert all(s.lstrip().upper().startswith("SELECT") for s in statements)
        with pytest.raises(ValidationError):
            p1.status = ImportPlanStatus.NOT_EXECUTABLE
        with pytest.raises(ValidationError):
            type(p1).model_validate({**p1.model_dump(), "unexpected": True})
        assert p1.model_dump_json() == p2.model_dump_json()
        assert preflight.model_dump_json() == original
        assert getcontext().prec == context.prec and getcontext().rounding == context.rounding
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", capture)
    assert counts(factory) == ([], [])


def test_frozen_tampering_authorization_drift_and_storage_fail_closed(db_session):
    factory, service = setup(db_session)
    admission, pf = frozen()
    authorization = auth(pf)
    changed_ready = (pf.ready_for_import_manifest[0].model_copy(update={"nominal_value": Decimal("999")}), *pf.ready_for_import_manifest[1:])
    digest = hashlib.sha256(json.dumps([r.model_dump(mode="json") for r in changed_ready],
        sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    rehashed_tamper = pf.model_copy(update={"ready_for_import_manifest": changed_ready,
        "ready_for_import_manifest_sha256": digest,
        "provenance": pf.provenance.model_copy(update={"ready_for_import_manifest_sha256": digest})})
    assert service.plan(preflight=rehashed_tamper, admission_manifest=admission,
                        authorization=auth(rehashed_tamper)).blockers == ("FROZEN_INPUT_INVALID",)
    for damaged in (
        pf.model_copy(update={"pit_ready": True}),
        pf.model_copy(update={"ready_for_import_manifest_sha256": "0" * 64}),
        pf.model_copy(update={"ready_for_import_manifest": pf.ready_for_import_manifest[::-1]}),
        pf.model_copy(update={"ready_for_import_manifest": ()}),
        pf.model_copy(update={"summary": pf.summary.model_copy(update={"ready_count": 100})}),
        pf.model_copy(update={"ready_for_import_manifest": (pf.ready_for_import_manifest[0].model_copy(update={"nominal_value": Decimal("999")}), *pf.ready_for_import_manifest[1:])}),
    ):
        assert service.plan(preflight=damaged, admission_manifest=admission, authorization=authorization).status is ImportPlanStatus.NOT_EXECUTABLE
    for overrides in ({"nominal_value": Decimal("1000.001")}, {"coupon_rate": Decimal("1.0001")},
                      {"nominal_value": Decimal("1000000000000")}, {"nominal_value": None}):
        a, p = frozen(overrides=overrides)
        assert service.plan(preflight=p, admission_manifest=a, authorization=auth(p)).status is ImportPlanStatus.NOT_EXECUTABLE
    a, empty = frozen(count=0)
    assert service.plan(preflight=empty, admission_manifest=a, authorization=auth(empty)).status is ImportPlanStatus.NOT_EXECUTABLE
    plan = service.plan(preflight=pf, admission_manifest=admission, authorization=authorization)
    assert service.apply(preflight=pf, admission_manifest=admission, authorization=authorization,
        reviewed_plan=plan, expected_plan_sha256=plan.plan_sha256, confirm_apply=1).status is ImportExecutionStatus.BLOCKED
    bad_auth = authorization.model_copy(update={"expected_ready_sha256": "0" * 64})
    assert service.plan(preflight=pf, admission_manifest=admission, authorization=bad_auth).status is ImportPlanStatus.NOT_EXECUTABLE
    with factory() as db:
        db.add(Company(name="Issuer Ltd", ticker="COLLISION", inn="7700000000"))
        db.commit()
    assert apply(service, pf, admission, plan).status is ImportExecutionStatus.BLOCKED
    assert counts(factory)[1] == []
    with factory() as db:
        company_id = db.scalar(select(Company.id))
        db.add(Bond(company_id=company_id, name="Partial collision", isin=pf.ready_for_import_manifest[0].isin,
                    secid="OTHER_SECID"))
        db.commit()
    blocked = service.plan(preflight=pf, admission_manifest=admission, authorization=authorization)
    assert "BOND_PARTIAL_COLLISION" in blocked.blockers
    with factory() as db:
        db.add(Bond(company_id=company_id, name="Identifier split", isin="RU000OTHER01",
                    secid=pf.ready_for_import_manifest[0].secid))
        db.add(Bond(company_id=company_id, name="Normalized variant", isin="\t" + pf.ready_for_import_manifest[1].isin.lower(),
                    secid="VARIANT"))
        db.commit()
    blocked = service.plan(preflight=pf, admission_manifest=admission, authorization=authorization)
    assert {"BOND_IDENTITY_CONFLICT", "NORMALIZED_IDENTIFIER_COLLISION"} <= set(blocked.blockers)


def test_authorized_apply_one_commit_shared_company_and_unrelated_unchanged(db_session):
    commits = []
    class CountingSession(Session):
        def commit(self):
            commits.append(1)
            return super().commit()
    unrelated = Company(name="Unrelated", ticker="OTHER", inn="9999999999")
    db_session.add(unrelated)
    db_session.flush()
    db_session.add(Bond(company_id=unrelated.id, name="Untouched", isin="RU000OTHER01", secid="OTHER"))
    db_session.commit()
    factory, service = setup(db_session, CountingSession)
    admission, pf = frozen()
    plan = service.plan(preflight=pf, admission_manifest=admission, authorization=auth(pf))
    result = apply(service, pf, admission, plan)
    assert result.status is ImportExecutionStatus.APPLIED
    assert result.committed_company_creates == 1 and result.committed_bond_creates == 2
    assert result.commit_count == len(commits) == 1
    assert result.pre_commit_audit.status is result.post_commit_audit.status is ImportAuditStatus.VERIFIED
    with factory() as db:
        imported = db.scalars(select(Bond).where(Bond.secid != "OTHER").order_by(Bond.isin)).all()
        assert len({b.company_id for b in imported}) == 1
        assert all(b.currency == "RUB" and b.nominal_value == Decimal("1000") for b in imported)
        assert all(b.is_floating_coupon is False and b.signal == "insufficient_data" for b in imported)
        old = db.scalar(select(Bond).where(Bond.secid == "OTHER"))
        assert old.name == "Untouched" and old.nominal_value is None
        assert not db.new and not db.dirty and not db.deleted


def test_repeat_noop_and_stale_plan_rejected(db_session):
    factory, service = setup(db_session)
    admission, pf = frozen()
    plan = service.plan(preflight=pf, admission_manifest=admission, authorization=auth(pf))
    assert apply(service, pf, admission, plan).status is ImportExecutionStatus.APPLIED
    before = counts(factory)
    assert apply(service, pf, admission, plan).status is ImportExecutionStatus.BLOCKED
    fresh = service.plan(preflight=pf, admission_manifest=admission, authorization=auth(pf))
    assert fresh.status is ImportPlanStatus.EXECUTABLE
    assert all(b.action is ImportAction.ALREADY_PRESENT_EXACT for b in fresh.bonds)
    result = apply(service, pf, admission, fresh)
    assert result.status is ImportExecutionStatus.APPLIED
    assert result.committed_bond_creates == result.committed_company_creates == 0
    assert result.db_mutated is False and counts(factory) == before


def test_atomic_rollback_lock_refusal_and_nonfresh_session(db_session):
    class FailingSession(Session):
        inserts = 0
        def flush(self, *args, **kwargs):
            if any(isinstance(obj, Bond) for obj in self.new):
                type(self).inserts += 1
                if type(self).inserts == 2:
                    raise RuntimeError("synthetic second INSERT failure")
            return super().flush(*args, **kwargs)
    factory, service = setup(db_session, FailingSession)
    admission, pf = frozen()
    plan = service.plan(preflight=pf, admission_manifest=admission, authorization=auth(pf))
    result = apply(service, pf, admission, plan)
    assert result.status is ImportExecutionStatus.ROLLED_BACK and result.rollback_confirmed
    assert result.attempted_company_creates == 1 and result.attempted_bond_creates == 2
    assert result.committed_bond_creates == result.committed_company_creates == 0
    assert counts(factory) == ([], [])
    class LockedSession(Session):
        def execute(self, statement, *args, **kwargs):
            if str(statement) == "BEGIN IMMEDIATE":
                raise RuntimeError("synthetic concurrent lock refusal")
            return super().execute(statement, *args, **kwargs)
    _, locked = setup(db_session, LockedSession)
    assert apply(locked, pf, admission, plan).status is ImportExecutionStatus.BLOCKED
    pending = Company(name="Caller owned", ticker="PENDING")
    db_session.add(pending)
    bad = ControlledBondImportService(lambda: db_session)
    assert apply(bad, pf, admission, plan).status is ImportExecutionStatus.BLOCKED
    assert pending in db_session.new and db_session.in_transaction()
    db_session.rollback()
    class CommitUnknownSession(Session):
        def commit(self):
            super().commit()
            raise RuntimeError("simulated lost acknowledgement after durable commit")
    unknown_factory, uncertain = setup(db_session, CommitUnknownSession)
    result = apply(uncertain, pf, admission, plan)
    assert result.status is ImportExecutionStatus.COMMIT_OUTCOME_UNKNOWN
    assert result.committed_bond_creates is None and result.db_mutated is None
    assert result.rollback_confirmed is False
    assert uncertain.audit(preflight=pf, admission_manifest=admission).status is ImportAuditStatus.VERIFIED
    # A new no-op plan is allowed, but failed post-commit verification is never
    # mislabeled as a rollback of its acknowledged commit.
    normal_factory, normal = setup(db_session)
    noop = normal.plan(preflight=pf, admission_manifest=admission, authorization=auth(pf))
    calls = []
    class BrokenReadSession(Session):
        def execute(self, *args, **kwargs):
            raise RuntimeError("synthetic post-commit read failure")
    broken_factory = sessionmaker(bind=db_session.get_bind(), class_=BrokenReadSession)
    def post_failure_factory():
        calls.append(1)
        return normal_factory() if len(calls) == 1 else broken_factory()
    failed_audit = apply(ControlledBondImportService(post_failure_factory), pf, admission, noop)
    assert failed_audit.status is ImportExecutionStatus.APPLIED_AUDIT_FAILED
    assert failed_audit.commit_count == 1 and not failed_audit.rollback_confirmed
    db_session.begin()
    assert apply(bad, pf, admission, plan).status is ImportExecutionStatus.BLOCKED
    assert db_session.in_transaction()
    db_session.rollback()


def test_audit_source_parity_missing_properties_and_static_boundary(db_session):
    factory, service = setup(db_session)
    admission, pf = frozen()
    TInvestBondImportPreflightService.validate_frozen_preflight(pf, admission)
    missing = service.audit(preflight=pf, admission_manifest=admission)
    assert missing.status is ImportAuditStatus.FAILED and missing.committed_bond_creates is None
    plan = service.plan(preflight=pf, admission_manifest=admission, authorization=auth(pf))
    receipt = apply(service, pf, admission, plan)
    audited = service.audit(preflight=pf, admission_manifest=admission, execution_result=receipt)
    assert audited.status is ImportAuditStatus.VERIFIED and audited.committed_bond_creates == 2
    assert audited.unexpected_mutation_count is None
    standalone = service.audit(preflight=pf, admission_manifest=admission)
    assert standalone.committed_company_creates is None
    with factory() as db:
        bond = db.scalar(select(Bond).where(Bond.id == receipt.created_bond_ids[0]))
        bond.name = "Changed"
        db.commit()
    assert service.audit(preflight=pf, admission_manifest=admission).status is ImportAuditStatus.FAILED
    assert service.plan(preflight=pf, admission_manifest=admission, authorization=auth(pf)).status is ImportPlanStatus.NOT_EXECUTABLE
    for issuer_override, board_override in (({}, {"observations": ()}), ({"issuer_title": None}, {})):
        descriptions = [description(c) for c in admission.import_candidate_manifest]
        review = _run_preflight(admission_manifest=admission, moex_descriptions=descriptions,
            internal_bonds=[], company_projections=[],
            issuer_overrides={c.isin: issuer_override for c in admission.import_candidate_manifest},
            board_overrides={c.isin: board_override for c in admission.import_candidate_manifest})
        assert review.summary.ready_count == 0
        TInvestBondImportPreflightService.validate_frozen_preflight(review, admission)
    path = Path(__file__).parents[1] / "app/services/controlled_bond_import_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {"sync", "lookup", "getenv", "requests", "httpx", "SessionLocal", "MoexBondUniverseService"}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & forbidden
