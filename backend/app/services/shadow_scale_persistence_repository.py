"""Scalar reads, lossless benchmark reconstruction and group audits."""
import json
from sqlalchemy import select, or_
from app.models.shadow_experiment_group import ShadowExperimentGroup as Group
from app.models.shadow_experiment_case import ShadowExperimentCase as Case
from app.models.shadow_experiment_benchmark import ShadowExperimentBenchmark as Benchmark
from app.models.shadow_experiment_benchmark_component import ShadowExperimentBenchmarkComponent as Component
from app.models.shadow_test_run import ShadowTestRun
from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_security_master_profile import BondSecurityMasterProfile
from app.schemas.shadow_experiment import OfzBenchmarkGenesisViewV1
from app.schemas.shadow_scale_activation import ShadowScaleActivationAuditV1 as Audit
from app.services import shadow_ledger_repository as ledger
from app.services.shadow_ledger_genesis_service import original_genesis_plan, genesis_fields
from app.schemas.shadow_ledger import ShadowGenesisRequestV1
from app.services.shadow_experiment_evaluator import validate_benchmark_genesis

TABLES = (Group, Case, Benchmark, Component)


def group_fields(matrix, key):
    return dict(group_key_sha256=key, status="ACTIVE", horizon_days=90, capital_case_count=7,
        **{name:getattr(matrix,name) for name in ("scale_genesis_sha256", "scale_policy_sha256",
        "experiment_policy_sha256", "genesis_date", "planned_end_date", "common_market_trade_date",
        "source_code_sha", "source_universe_sha256", "investment_batch_sha256")})


def case_fields(case, ordinal):
    return dict(ordinal=ordinal, capital_rub=case.capital_rub, case_sha256=case.case_sha256,
        experiment_genesis_sha256=case.experiment_genesis.experiment_genesis_sha256,
        strategy_genesis_plan_sha256=case.reviewed_strategy_genesis.plan_sha256,
        shadow_execution_sha256=case.experiment_genesis.shadow_execution_sha256,
        benchmark_genesis_sha256=case.experiment_genesis.benchmark_genesis_sha256,
        initial_realized_invested_weight=case.shadow_realized_invested_weight,
        initial_cash_weight=case.shadow_realized_cash_weight, initial_selected_count=case.shadow_planned_position_count,
        initial_target_duration_years=case.target_duration_years)


def benchmark_fields(benchmark):
    names=("benchmark_genesis_sha256", "contract_version", "experiment_policy_sha256",
        "shadow_execution_sha256", "genesis_date", "planned_end_date", "common_market_trade_date",
        "initial_capital_rub", "target_duration_years", "reconstructed_duration_years", "matching_mode", "genesis_nav_rub")
    return dict(**{name:getattr(benchmark,name) for name in names}, component_count=len(benchmark.components),
        benchmark_payload_json=benchmark.model_dump(mode="json"))


def component_fields(component, ordinal):
    r=component.representative
    return dict(ordinal=ordinal, bond_id=r.bond_id, market_snapshot_id=r.snapshot_id,
        security_master_profile_id=component.genesis_market_evidence.provenance.security_master_profile_id,
        isin=r.isin, secid=r.secid, weight=component.weight,
        source_node_macaulay_duration_years=r.source_node_macaulay_duration_years,
        modified_duration_years=r.modified_duration_years, source_node_yield_pct=r.source_node_yield_pct,
        component_yield_pct=r.component_yield_pct, genesis_dirty_value_rub=component.genesis_dirty_value_rub,
        component_sha256=ledger.digest(component))


def check_references(db, benchmark):
    with db.no_autoflush:
        for c in benchmark.components:
            r=c.representative; fields=component_fields(c,1)
            pid=fields["security_master_profile_id"]
            ledger.require(type(pid) is int and pid>0, "SOURCE_REFERENCE_MISSING")
            b=db.execute(select(Bond.isin,Bond.secid).where(Bond.id==r.bond_id)).one_or_none()
            m=db.execute(select(BondMarketSnapshot.bond_id,BondMarketSnapshot.trade_date,BondMarketSnapshot.source)
                .where(BondMarketSnapshot.id==r.snapshot_id)).one_or_none()
            p=db.execute(select(BondSecurityMasterProfile.bond_id).where(BondSecurityMasterProfile.id==pid)).scalar_one_or_none()
            ledger.require(b == (r.isin,r.secid) and m == (r.bond_id,benchmark.common_market_trade_date,"moex") and
                p==r.bond_id, "SOURCE_REFERENCE_MISSING")


def read_state(db, matrix, key, child_keys):
    with db.no_autoflush:
        groups=ledger.rows(db,Group,or_(Group.group_key_sha256==key,
            Group.scale_genesis_sha256==matrix.scale_genesis_sha256),(Group.group_key_sha256,))
        gids=tuple(g["id"] for g in groups)
        cases=ledger.rows(db,Case,Case.experiment_group_id.in_(gids),(Case.ordinal,Case.case_sha256)) if gids else []
        cids=tuple(c["id"] for c in cases)
        benchmarks=ledger.rows(db,Benchmark,Benchmark.experiment_case_id.in_(cids),(Benchmark.benchmark_genesis_sha256,)) if cids else []
        bids=tuple(b["id"] for b in benchmarks)
        components=ledger.rows(db,Component,Component.benchmark_id.in_(bids),(Component.ordinal,Component.component_sha256)) if bids else []
        linked=tuple(c["shadow_run_id"] for c in cases)
        linked_runs=ledger.rows(db,ShadowTestRun,ShadowTestRun.id.in_(linked),(ShadowTestRun.run_key_sha256,)) if linked else []
        keys=tuple(sorted(set(child_keys)|{r["run_key_sha256"] for r in linked_runs}))
        children={k:ledger.read_state(db,k) for k in keys}
        return dict(groups=groups,cases=cases,benchmarks=benchmarks,components=components,children=children)


def state_hash(state):
    groups={r["id"]:r["group_key_sha256"] for r in state["groups"]}
    cases={r["id"]:r["case_sha256"] for r in state["cases"]}
    benchmarks={r["id"]:r["benchmark_genesis_sha256"] for r in state["benchmarks"]}
    runs={v["run"]["id"]:k for k,v in state["children"].items() if v["run"]}
    mappings={"experiment_group_id":groups,"experiment_case_id":cases,"benchmark_id":benchmarks,"shadow_run_id":runs}
    def clean(row):
        return {k:mappings[k].get(v,"UNRESOLVED_LINK") if k in mappings else v
            for k,v in row.items() if k!="id"}
    return ledger.digest({**{k:[clean(r) for r in state[k]] for k in ("groups","cases","benchmarks","components")},
        "children":{k:ledger.state_hash(v) for k,v in state["children"].items()}})


def _equal(row, expected):
    ledger.require(all(row.get(k)==v for k,v in expected.items()),"HISTORICAL_SCALE_STATE_DRIFT")


def _reconstruct(db, row):
    try:
        payload=row["benchmark_payload_json"]
        ledger.require(type(payload) is dict,"BENCHMARK_PERSISTENCE_INVALID")
        value=OfzBenchmarkGenesisViewV1.model_validate_json(json.dumps(payload,allow_nan=False))
        validate_benchmark_genesis(value)
        _equal(row,benchmark_fields(value))
        cs=ledger.rows(db,Component,Component.benchmark_id==row["id"],(Component.ordinal,))
        ledger.require(len(cs)==len(value.components),"BENCHMARK_PERSISTENCE_INVALID")
        for ordinal,(actual,component) in enumerate(zip(cs,value.components),1):
            _equal(actual,component_fields(component,ordinal))
        return value
    except Exception:
        raise ValueError("BENCHMARK_PERSISTENCE_INVALID") from None


def load_persisted_benchmark_genesis(db, *, experiment_case_id):
    ledger.require(type(experiment_case_id) is int and experiment_case_id>0,"BENCHMARK_PERSISTENCE_INVALID")
    with db.no_autoflush:
        cases=ledger.rows(db,Case,Case.id==experiment_case_id,(Case.id,))
        found=ledger.rows(db,Benchmark,Benchmark.experiment_case_id==experiment_case_id,(Benchmark.id,))
        ledger.require(len(cases)==len(found)==1,"BENCHMARK_PERSISTENCE_INVALID")
        value=_reconstruct(db,found[0])
        ledger.require(cases[0]["capital_rub"]==value.initial_capital_rub and
            cases[0]["benchmark_genesis_sha256"]==value.benchmark_genesis_sha256 and
            cases[0]["shadow_execution_sha256"]==value.shadow_execution_sha256,"BENCHMARK_PERSISTENCE_INVALID")
        return value


def audit_group(db, matrix, key, children):
    try:
        state=read_state(db,matrix,key,tuple(p.run_key_sha256 for p in children))
        ledger.require(len(state["groups"])==1,"HISTORICAL_SCALE_STATE_DRIFT")
        g=state["groups"][0]; _equal(g,group_fields(matrix,key))
        ledger.require(len(state["cases"])==len(state["benchmarks"])==7,"PARTIAL_SCALE_ACTIVATION_STATE")
        case_ids=[]; run_ids=[]; benchmark_ids=[]; count=0
        for ordinal,(case,child) in enumerate(zip(matrix.cases,children),1):
            row=state["cases"][ordinal-1]; _equal(row,case_fields(case,ordinal))
            ledger.require(row["experiment_group_id"]==g["id"],"HISTORICAL_SCALE_STATE_DRIFT")
            original=original_genesis_plan(child)
            ledger.require(original==case.reviewed_strategy_genesis,"CHILD_GENESIS_PLAN_MISMATCH")
            child_state=state["children"][child.run_key_sha256]
            ledger.require(child_state["run"] is not None and child_state["run"]["id"]==row["shadow_run_id"] and
                child_state["run"]["initial_capital_rub"]==case.capital_rub and
                ledger.audit_genesis(db,original).status=="VERIFIED","HISTORICAL_SCALE_STATE_DRIFT")
            request=ShadowGenesisRequestV1(shadow_execution=case.source_shadow_execution,source_code_sha=matrix.source_code_sha)
            _equal(child_state["run"],genesis_fields(original,request))
            ledger.require(len(child_state["ledger"])==len(original.events) and
                len(child_state["snapshots"])==1 and len(child_state["positions"])==len(original.positions),
                "HISTORICAL_SCALE_STATE_DRIFT")
            for actual,expected in zip(child_state["ledger"],original.events):
                _equal(actual,expected.model_dump(exclude={"pit_ready"}))
                ledger.require(actual["details_json"]==expected.model_dump(mode="json"),"HISTORICAL_SCALE_STATE_DRIFT")
            for actual,expected in zip(sorted(child_state["positions"],key=lambda p:p["bond_id"]),original.positions):
                _equal(actual,expected.model_dump(exclude={"pit_ready"}))
            snapshot=child_state["snapshots"][0]
            ledger.require(snapshot["details_json"]==dict(snapshot=original.snapshot.model_dump(mode="json"),
                positions=[p.model_dump(mode="json") for p in original.positions],
                provenance_versions=list(ledger.PROVENANCE_VERSIONS),calculation_context="DECIMAL_28_ROUND_HALF_EVEN",
                hash_method="SORTED_CANONICAL_JSON_SHA256_V1",diagnostics=list(original.diagnostics),
                valuation_evidence=[v.model_dump(mode="json") for v in original.valuation_evidence]),
                "HISTORICAL_SCALE_STATE_DRIFT")
            matches=[b for b in state["benchmarks"] if b["experiment_case_id"]==row["id"]]
            ledger.require(len(matches)==1,"PARTIAL_SCALE_ACTIVATION_STATE")
            b=matches[0]; ledger.require(_reconstruct(db,b)==case.experiment_genesis.benchmark,"HISTORICAL_SCALE_STATE_DRIFT")
            check_references(db,case.experiment_genesis.benchmark)
            case_ids.append(row["id"]);run_ids.append(row["shadow_run_id"]);benchmark_ids.append(b["id"])
            count+=b["component_count"]
        ledger.require(len(state["components"])==count and len(state["children"])==7,"HISTORICAL_SCALE_STATE_DRIFT")
        return Audit(status="VERIFIED",group_id=g["id"],case_ids=tuple(case_ids),child_run_ids=tuple(run_ids),
            benchmark_ids=tuple(benchmark_ids),benchmark_component_count=count,verified_child_count=7)
    except Exception:
        return Audit(status="FAILED",blockers=("HISTORICAL_SCALE_STATE_DRIFT",))
