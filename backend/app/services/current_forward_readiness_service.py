"""One caller-owned read-only snapshot; no source clients or mutation paths."""
from dataclasses import replace

from sqlalchemy import Connection, select, text
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.schemas.tinvest_bond_identity_bridge import BondIdentityProjection
from app.schemas.portfolio_strategy import PortfolioStrategyRequest
from app.schemas.shadow_scale_experiment import CAPITAL_GRID_RUB, ShadowScaleExperimentPolicyV1, ShadowScaleExperimentCaseInputV1
from app.schemas.shadow_experiment import ShadowExperimentPolicyV1
from app.services.investment_model_snapshot_service import InvestmentModelSnapshotService
from app.services.risk_candidate_reducer import RiskCandidateReducer, validate_investment_batch
from app.services.portfolio_strategy_builder import PortfolioStrategyBuilder
from app.services.shadow_execution_terms_loader import ShadowExecutionTermsLoader
from app.services.shadow_execution_planner import ShadowExecutionPlanner
from app.services.shadow_experiment_genesis_service import ShadowExperimentGenesisService
from app.services.shadow_scale_experiment_genesis_service import ShadowScaleExperimentGenesisService
from app.services import current_forward_readiness_report as report


INPUT_CODES = frozenset(("REQUEST_INVALID", "ACCOUNT_UID_CONFLICT", "ACCOUNT_HASH_MISMATCH", "ACCOUNT_TESTS_INVALID",
    "ACCOUNT_UID_UNKNOWN", "SOURCE_HASH_MISMATCH", "SOURCE_ENVELOPE_INCONSISTENT", "GENESIS_CAPITAL_INVALID"))


def readonly_connection(db):
    """Inspect only: never begin/upgrade/rollback the caller's transaction."""
    bind = db.get_bind()
    if not db.in_transaction() and not (isinstance(bind, Connection) and bind.in_transaction()):
        return None
    connection = db.connection()
    dialect = connection.dialect.name
    if dialect == "sqlite":
        if connection.exec_driver_sql("PRAGMA query_only").scalar_one() != 1:
            return None
        return connection if connection.connection.driver_connection.in_transaction else None
    if dialect == "postgresql":
        isolation, readonly = connection.execute(text(
            "SELECT current_setting('transaction_isolation'), current_setting('transaction_read_only')")).one()
        return connection if isolation in ("repeatable read", "serializable") and readonly == "on" else None
    return None


class CurrentForwardReadinessService:
    def __init__(self, db):
        self.db = db

    def build(self, *, request):
        try:
            request = report.normalize_request(request)
        except Exception as exc:
            return report.blocked(str(exc) if str(exc) in INPUT_CODES else "SOURCE_INPUT_INVALID")
        try:
            with self.db.no_autoflush:
                connection = readonly_connection(self.db)
                if connection is None:
                    return report.blocked("READ_ONLY_SNAPSHOT_REQUIRED", request)
                # Excludes caller-owned dirty ORM values without changing its
                # identity map or ownership of the physical snapshot.
                with Session(bind=connection, join_transaction_mode="rollback_only", autoflush=False) as isolated:
                    return self._build(isolated, request)
        except Exception:
            return report.blocked("READ_ONLY_DEPENDENCY_FAILURE", request)

    def _build(self, db, request):
        with db.no_autoflush:
            canonical = tuple(BondIdentityProjection(bond_id=r.id, isin=r.isin, secid=r.secid) for r in db.execute(
                select(Bond.id, Bond.isin, Bond.secid).where(Bond.id.in_(request.bond_ids)).order_by(Bond.id)))
            try:
                investment = InvestmentModelSnapshotService(db).build(request.bond_ids, request.as_of_date,
                    market_source=request.market_source, max_market_age_days=request.max_market_age_days,
                    max_curve_age_days=request.max_curve_age_days, liquidity_lookback_calendar_days=request.liquidity_lookback_calendar_days,
                    liquidity_min_observation_days=request.liquidity_min_observation_days)
                validate_investment_batch(investment)
                source = investment.source_batch
                report.require((source.requested_bond_ids, investment.as_of_date, investment.market_source,
                    source.max_market_age_days, source.max_curve_age_days, source.liquidity_lookback_calendar_days,
                    source.liquidity_min_observation_days) == (request.bond_ids, request.as_of_date, "moex",7,7,30,5),
                    "INVESTMENT_CONTEXT_CONFLICT")
                current = {p.bond_id:p for p in canonical}
                report.require(set(source.existing_bond_ids) == set(current), "INVESTMENT_IDENTITY_CONFLICT")
                for evaluation in investment.evaluations:
                    row = current[evaluation.bond_id]
                    report.require((evaluation.isin,evaluation.secid) == (row.isin,row.secid), "INVESTMENT_IDENTITY_CONFLICT")
            except Exception:
                return report.build_report(request, canonical, None, None,
                    tuple(report.CaseWork(c, errors=("INVESTMENT_INPUT_INVALID_OR_UNAVAILABLE",)) for c in CAPITAL_GRID_RUB),
                    errors=("INVESTMENT_INPUT_INVALID_OR_UNAVAILABLE",))
            try:
                risk = RiskCandidateReducer.build(investment)
            except Exception:
                return report.build_report(request, canonical, investment, None,
                    tuple(report.CaseWork(c, errors=("RISK_INPUT_INVALID_OR_UNAVAILABLE",)) for c in CAPITAL_GRID_RUB),
                    errors=("RISK_INPUT_INVALID_OR_UNAVAILABLE",))
            work = []
            plans = {p.capital_rub:p.plan for p in request.reviewed_genesis}
            for capital in CAPITAL_GRID_RUB:
                strategy = execution = None
                try:
                    strategy = PortfolioStrategyBuilder.build(investment,risk,PortfolioStrategyRequest(capital_rub=capital))
                    report.require(strategy.source_investment_batch == investment and strategy.source_risk_batch == risk,
                        "COMMON_EVIDENCE_CONFLICT")
                    terms = ShadowExecutionTermsLoader(db).build(strategy)
                    execution = ShadowExecutionPlanner.build(strategy,terms)
                    report.require(execution.source_strategy == strategy and execution.as_of_date == request.as_of_date,
                        "COMMON_EVIDENCE_CONFLICT")
                    work.append(report.CaseWork(capital,strategy,execution))
                except Exception:
                    work.append(report.CaseWork(capital,strategy,execution,errors=("CASE_EVIDENCE_INVALID_OR_UNAVAILABLE",)))
            errors = []
            valid = []
            for index,w in enumerate(work):
                plan = plans.get(w.capital)
                if plan is None or w.execution is None:
                    continue
                if plan.source_code_sha != request.implementation_sha or plan.shadow_execution_sha256 != report.native_fingerprint(w.execution):
                    work[index] = replace(w, errors=w.errors+("REVIEWED_GENESIS_EVIDENCE_CONFLICT",))
                    errors.append("REVIEWED_GENESIS_EVIDENCE_CONFLICT")
                    continue
                valid.append((index, ShadowScaleExperimentCaseInputV1(capital_rub=w.capital,
                    reviewed_strategy_genesis=plan,source_shadow_execution=w.execution)))
            if len(valid) == len(CAPITAL_GRID_RUB):
                try:
                    matrix = ShadowScaleExperimentGenesisService(db).build(cases=tuple(c for _,c in valid),
                        scale_policy=ShadowScaleExperimentPolicyV1())
                    report.require(tuple(c.capital_rub for c in matrix.cases) == CAPITAL_GRID_RUB,"COMMON_EVIDENCE_CONFLICT")
                    for (index,_),case in zip(valid,matrix.cases):
                        report.require(case.source_shadow_execution == work[index].execution,"COMMON_EVIDENCE_CONFLICT")
                        work[index] = replace(work[index],experiment=case.experiment_genesis)
                    errors.extend(matrix.blockers)
                except Exception:
                    errors.append("BENCHMARK_COMMON_EVIDENCE_CONFLICT")
            else:
                for index,case in valid:
                    try:
                        experiment = ShadowExperimentGenesisService(db).build(reviewed_genesis_plan=case.reviewed_strategy_genesis,
                            shadow_execution=case.source_shadow_execution,policy=ShadowExperimentPolicyV1())
                        report.require(experiment.source_shadow_execution == case.source_shadow_execution,"COMMON_EVIDENCE_CONFLICT")
                        work[index] = replace(work[index],experiment=experiment)
                    except Exception:
                        work[index] = replace(work[index],errors=work[index].errors+("BENCHMARK_EVIDENCE_INVALID_OR_UNAVAILABLE",))
            return report.build_report(request,canonical,investment,risk,tuple(work),errors=tuple(errors))
