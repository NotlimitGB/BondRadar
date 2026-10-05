"""Explicit authorization of one atomic seven-capital activation transaction."""
from copy import deepcopy
from sqlalchemy import text, event
from app.schemas.shadow_scale_activation import (
    ShadowScaleActivationRequestV1 as Request, ShadowScaleActivationPlanV1 as Plan,
    ShadowScaleActivationCasePlanV1 as CasePlan, ShadowScaleActivationAuthorizationV1 as Authorization,
    ShadowScaleActivationReceiptV1 as Receipt, Blocker,
)
from app.schemas.shadow_ledger import ShadowGenesisRequestV1
from app.schemas.shadow_scale_experiment import ShadowScaleExperimentGenesisPlanV1
from app.services import shadow_ledger_repository as ledger
from app.services import shadow_scale_persistence_repository as store
from app.services.shadow_ledger_genesis_service import (
    build_genesis_plan_in_session, original_genesis_plan, genesis_fields,
)
from app.services.shadow_scale_experiment_evaluator import validate_scale_genesis


def validate_request(request):
    # Validate the wrapper once; the authoritative validator performs complete
    # original-type/schema/hash validation of the entire attached matrix.
    ledger.require(type(request) is Request and set(request.__dict__) <= set(Request.model_fields) and
        request.__pydantic_extra__ in (None,{}) and
        request.contract_version=="shadow-scale-activation-request-v1" and request.pit_ready is False,
        "INPUT_INVALID")
    matrix=request.scale_genesis
    ledger.require(type(matrix) is ShadowScaleExperimentGenesisPlanV1,"INPUT_INVALID")
    # Preserve original Python types and internal aliases, but detach every
    # caller-owned mutable container before validating the private snapshot.
    snapshot=deepcopy(matrix)
    validate_scale_genesis(snapshot)
    ledger.require(snapshot.status=="READY" and snapshot.ready_case_count==7 and
        snapshot.blocked_case_count==0,"SCALE_GENESIS_NOT_READY")
    return snapshot



def identity(matrix):
    names=("scale_genesis_sha256","scale_policy_sha256","experiment_policy_sha256","genesis_date",
        "planned_end_date","common_market_trade_date","source_code_sha","source_universe_sha256",
        "investment_batch_sha256","capital_grid_rub")
    fields={n:getattr(matrix,n) for n in names}
    return dict(group_key_sha256=ledger.digest(fields),**fields)


def child_request(matrix,case):
    return ShadowGenesisRequestV1(shadow_execution=case.source_shadow_execution,source_code_sha=matrix.source_code_sha)


def _build_plan_in_session(db, *, request, matrix):
    # Matrix has passed full immutable-input validation at the public boundary.
    base=identity(matrix); key=base["group_key_sha256"]
    children=[]; cases=[]; blockers=set()
    with db.no_autoflush:
        for ordinal,c in enumerate(matrix.cases,1):
            child=build_genesis_plan_in_session(db,request=child_request(matrix,c));children.append(child)
            original=original_genesis_plan(child)
            if original!=c.reviewed_strategy_genesis:
                blockers.add("CHILD_GENESIS_PLAN_MISMATCH")
            if child.status=="BLOCKED":blockers.add("HISTORICAL_SCALE_STATE_DRIFT")
            store.check_references(db,c.experiment_genesis.benchmark)
            cases.append(CasePlan(ordinal=ordinal,capital_rub=c.capital_rub,case_sha256=c.case_sha256,
                experiment_genesis_sha256=c.experiment_genesis.experiment_genesis_sha256,
                strategy_genesis_plan_sha256=c.reviewed_strategy_genesis.plan_sha256,
                shadow_execution_sha256=c.experiment_genesis.shadow_execution_sha256,
                benchmark_genesis_sha256=c.experiment_genesis.benchmark_genesis_sha256,
                child_run_key_sha256=child.run_key_sha256,child_genesis_plan_sha256=original.plan_sha256,
                child_current_db_state_sha256=child.current_shadow_db_state_sha256,child_status=child.status,
                benchmark_component_count=len(c.experiment_genesis.benchmark.components),
                blockers=("HISTORICAL_SCALE_STATE_DRIFT",) if child.status=="BLOCKED" else ()))
        state=store.read_state(db,matrix,key,tuple(p.run_key_sha256 for p in children))
        status="EXECUTABLE"
        if not state["groups"]:
            if any(c["run"] for c in state["children"].values()):blockers.add("ORPHAN_CHILD_RUN_CONFLICT")
        else:
            expected_components=sum(len(c.experiment_genesis.benchmark.components) for c in matrix.cases)
            if (len(state["cases"])!=7 or len(state["benchmarks"])!=7 or
                len(state["components"])!=expected_components or
                any(not state["children"][p.run_key_sha256]["run"] for p in children)):
                blockers.add("PARTIAL_SCALE_ACTIVATION_STATE")
            elif store.audit_group(db,matrix,key,children).status=="VERIFIED":status="IDEMPOTENT_NOOP"
            else:blockers.add("HISTORICAL_SCALE_STATE_DRIFT")
        if blockers:status="BLOCKED"
        plan=Plan(status=status,**base,cases=tuple(cases),current_db_state_sha256=store.state_hash(state),
            input_state_sha256=ledger.digest(request),blockers=tuple(sorted(blockers)))
        return ledger.signed_plan(plan),tuple(children)


def authorization_fields(plan):
    return dict(**{n:getattr(plan,n) for n in ("plan_sha256","group_key_sha256","scale_genesis_sha256",
        "current_db_state_sha256","input_state_sha256")},
        case_sha256s=tuple(c.case_sha256 for c in plan.cases),
        child_run_key_sha256s=tuple(c.child_run_key_sha256 for c in plan.cases),
        child_genesis_plan_sha256s=tuple(c.child_genesis_plan_sha256 for c in plan.cases),
        benchmark_genesis_sha256s=tuple(c.benchmark_genesis_sha256 for c in plan.cases))


def acquire_locks(db,key):
    dialect=db.get_bind().dialect.name
    ledger.require(dialect in ("sqlite","postgresql"),"APPLY_DIALECT_UNSUPPORTED")
    try:
        if dialect=="sqlite":db.execute(text("BEGIN IMMEDIATE"))
        else:
            db.begin()
            value=int(key[:16],16); value=value-(1<<64) if value>=(1<<63) else value
            ledger.require(db.execute(text("SELECT pg_try_advisory_xact_lock(:key)"),{"key":value}).scalar_one() is True,
                "LOCK_OR_TRANSACTION_ERROR")
            for model in (*store.TABLES,*ledger.TABLES):
                db.execute(text(f"LOCK TABLE {model.__tablename__} IN SHARE ROW EXCLUSIVE MODE NOWAIT"))
            for table in ("bonds","bond_market_snapshots","bond_security_master_profiles"):
                db.execute(text(f"LOCK TABLE {table} IN SHARE MODE NOWAIT"))
    except Exception:
        raise ValueError("LOCK_OR_TRANSACTION_ERROR") from None


def persist_group(db,matrix,key,children):
    group=store.Group(**store.group_fields(matrix,key));db.add(group);db.flush()
    for ordinal,(case,child) in enumerate(zip(matrix.cases,children),1):
        ledger.persist(db,child,genesis_fields(child,child_request(matrix,case)))
        run=ledger.read_state(db,child.run_key_sha256)["run"]
        row=store.Case(experiment_group_id=group.id,shadow_run_id=run["id"],**store.case_fields(case,ordinal))
        db.add(row);db.flush()
        benchmark=case.experiment_genesis.benchmark
        b=store.Benchmark(experiment_case_id=row.id,**store.benchmark_fields(benchmark));db.add(b);db.flush()
        for n,component in enumerate(benchmark.components,1):
            db.add(store.Component(benchmark_id=b.id,**store.component_fields(component,n)))
    db.flush()


def _code(exc,fallback="INPUT_INVALID"):
    return str(exc) if str(exc) in Blocker.__args__ else fallback


class ShadowScaleActivationService:
    def __init__(self,session_factory):
        self.session_factory=session_factory

    def plan(self, *, request):
        try:
            matrix=validate_request(request)
            request=Request(scale_genesis=matrix)
            db=ledger.fresh(self.session_factory)
        except Exception as exc:return Plan(status="BLOCKED",blockers=(_code(exc),))
        try:return _build_plan_in_session(db,request=request,matrix=matrix)[0]
        except Exception as exc:return Plan(status="BLOCKED",blockers=(_code(exc),))
        finally:db.close()

    def apply(self, *, request, reviewed_plan, authorization):
        try:
            matrix=validate_request(request);request=Request(scale_genesis=matrix)
            ledger.strict(reviewed_plan,Plan);ledger.strict(authorization,Authorization)
            ledger.require(reviewed_plan.status in ("EXECUTABLE","IDEMPOTENT_NOOP") and not reviewed_plan.blockers,
                "AUTHORIZATION_MISMATCH")
            ledger.require(reviewed_plan.plan_sha256==ledger.signed_plan(reviewed_plan).plan_sha256,"PLAN_SHA_MISMATCH")
            expected=Authorization(explicit_apply=True,**authorization_fields(reviewed_plan))
            ledger.require(authorization==expected,"AUTHORIZATION_MISMATCH")
            ledger.require(reviewed_plan.input_state_sha256==ledger.digest(request) and
                reviewed_plan.group_key_sha256==identity(matrix)["group_key_sha256"],"AUTHORIZATION_MISMATCH")
            db=ledger.fresh(self.session_factory)
        except Exception as exc:return Receipt(status="BLOCKED",blockers=(_code(exc),))
        component_count=sum(len(c.experiment_genesis.benchmark.components) for c in matrix.cases)
        base=dict(group_key_sha256=reviewed_plan.group_key_sha256,plan_sha256=reviewed_plan.plan_sha256,
            planned_group_count=1,planned_case_count=7,planned_child_run_count=7,planned_benchmark_count=7,
            planned_component_count=component_count)
        phase="locks";attempted=[0];pre=None;committed=False;children=();baseline_hash=None
        def count_inserts(session, flush_context, instances):
            # Counts records submitted for INSERT at flush, including a failed flush.
            attempted[0] += len(session.new)
        event.listen(db,"before_flush",count_inserts)
        try:
            acquire_locks(db,reviewed_plan.group_key_sha256)
            phase="revalidate"
            rebuilt,children=_build_plan_in_session(db,request=request,matrix=matrix)
            baseline_hash=rebuilt.current_db_state_sha256
            ledger.require(rebuilt.current_db_state_sha256==reviewed_plan.current_db_state_sha256,"CURRENT_SCALE_STATE_DRIFT")
            ledger.require(rebuilt==reviewed_plan,"CURRENT_SCALE_STATE_DRIFT")
            if rebuilt.status=="IDEMPOTENT_NOOP":
                pre=store.audit_group(db,matrix,rebuilt.group_key_sha256,children)
                ledger.require(pre.status=="VERIFIED","PRE_COMMIT_AUDIT_FAILED")
                return Receipt(status="IDEMPOTENT_NOOP",**base,group_id=pre.group_id,case_ids=pre.case_ids,
                    child_run_ids=pre.child_run_ids,benchmark_ids=pre.benchmark_ids,pre_commit_audit=pre,post_commit_audit=pre)
            phase="persist"
            persist_group(db,matrix,rebuilt.group_key_sha256,children)
            pre=store.audit_group(db,matrix,rebuilt.group_key_sha256,children)
            ledger.require(pre.status=="VERIFIED","PRE_COMMIT_AUDIT_FAILED")
            phase="commit";db.commit();committed=True;phase="post_audit"
            db.close()
            audit_db=ledger.fresh(self.session_factory)
            try:
                ledger.require(audit_db is not db,"SESSION_NOT_FRESH")
                post=store.audit_group(audit_db,matrix,rebuilt.group_key_sha256,children)
            finally:audit_db.close()
            status="APPLIED" if post.status=="VERIFIED" else "POST_COMMIT_AUDIT_FAILED"
            return Receipt(status=status,**base,group_id=pre.group_id,case_ids=pre.case_ids,
                child_run_ids=pre.child_run_ids,benchmark_ids=pre.benchmark_ids,attempted_mutation_count=attempted[0],
                committed_mutation_count=attempted[0],committed_group_count=1,committed_case_count=7,
                committed_child_run_count=7,committed_benchmark_count=7,committed_component_count=component_count,
                commit_count=1,pre_commit_audit=pre,post_commit_audit=post,
                blockers=() if status=="APPLIED" else ("POST_COMMIT_AUDIT_FAILED",))
        except Exception as exc:
            if committed:
                return Receipt(status="POST_COMMIT_AUDIT_FAILED",**base,attempted_mutation_count=attempted[0],
                    committed_mutation_count=attempted[0],committed_group_count=1,committed_case_count=7,
                    committed_child_run_count=7,committed_benchmark_count=7,committed_component_count=component_count,
                    commit_count=1,pre_commit_audit=pre,group_id=pre.group_id,case_ids=pre.case_ids,
                    child_run_ids=pre.child_run_ids,benchmark_ids=pre.benchmark_ids,blockers=("POST_COMMIT_AUDIT_FAILED",))
            if phase=="commit":
                return Receipt(status="COMMIT_OUTCOME_UNKNOWN",**base,attempted_mutation_count=attempted[0],
                    committed_mutation_count=None,committed_group_count=None,committed_case_count=None,
                    committed_child_run_count=None,committed_benchmark_count=None,committed_component_count=None,
                    commit_count=None,pre_commit_audit=pre,blockers=("COMMIT_OUTCOME_UNKNOWN",))
            try:
                db.rollback()
                if children:
                    state=store.read_state(db,matrix,reviewed_plan.group_key_sha256,tuple(p.run_key_sha256 for p in children))
                    ledger.require(store.state_hash(state)==baseline_hash,"ROLLBACK_FAILED")
                confirmed=True
            except Exception:confirmed=False
            return Receipt(status="ROLLED_BACK" if phase=="persist" and confirmed else "BLOCKED",**base,
                attempted_mutation_count=attempted[0],committed_mutation_count=0 if confirmed else None,
                committed_group_count=0 if confirmed else None,committed_case_count=0 if confirmed else None,
                committed_child_run_count=0 if confirmed else None,committed_benchmark_count=0 if confirmed else None,
                committed_component_count=0 if confirmed else None,rollback_confirmed=confirmed,pre_commit_audit=pre,
                blockers=(_code(exc,"PRE_COMMIT_AUDIT_FAILED") if confirmed else "ROLLBACK_FAILED",))
        finally:
            event.remove(db,"before_flush",count_inserts)
            db.close()
