"""Pure diagnostics of supplied source facts and authoritative modern results."""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from pydantic import BaseModel

from app.schemas import current_forward_readiness as s
from app.schemas.shadow_scale_experiment import CAPITAL_GRID_RUB
from app.services.historical_audit_canonical_json import digest
from app.services.tinvest_frozen_admission_evidence_service import TInvestFrozenAdmissionEvidenceService
from app.services.tinvest_bond_import_preflight_service import TInvestBondImportPreflightService, _INACTIVE_STATUS_VALUES, _parse_bool
from app.services.unified_candidate_reducer import CORE_REASONS
from app.services.shadow_experiment_evaluator import digest as native_fingerprint


DECISIONS = s.DECISION_NAMES
UNKNOWN = "SOURCE_FACT_UNKNOWN"
VERIFIED = "SOURCE_FACT_VERIFIED"
CONFLICT = "SOURCE_FACT_CONFLICT"
UNAVAILABLE = "SOURCE_FACT_UNAVAILABLE"


def require(condition, code):
    if not condition:
        raise ValueError(code)


def fingerprint(value):
    # Legacy admission contracts contain StrEnums. Use their JSON-mode values
    # before the shared iterator; do not modify its frozen implementation.
    return digest(value.model_dump(mode="json", warnings=False) if isinstance(value, BaseModel) else value, exclude=())


def account_fingerprint(value):
    return digest(value, exclude=("evidence_sha256",), overrides={"passed_tests":tuple(sorted(value.passed_tests))})


def normalize_request(request):
    require(type(request) is s.CurrentForwardReadinessRequest, "REQUEST_INVALID")
    require(set(request.__dict__) <= set(type(request).model_fields) and request.__pydantic_extra__ in (None, {}), "REQUEST_INVALID")
    # JSON serialization can coerce model_copy(bool-as-int) into an integer.
    # Validate original Python types before detaching through JSON.
    s.CurrentForwardReadinessRequest.model_validate(request.model_dump(warnings=False))
    # Reparse to detach mutable native source dictionaries and validate nested
    # model_copy/model_construct inputs, including old frozen contracts.
    request = s.CurrentForwardReadinessRequest.model_validate_json(request.model_dump_json(warnings=False))
    context = request.source_context
    if context is not None:
        accounts = context.accounts
        require(len({a.uid for a in accounts}) == len(accounts), "ACCOUNT_UID_CONFLICT")
        for account in accounts:
            require(account_fingerprint(account) == account.evidence_sha256, "ACCOUNT_HASH_MISMATCH")
            require(len(set(account.passed_tests)) == len(account.passed_tests) and
                all(type(t) is str and bool(t.strip()) for t in account.passed_tests), "ACCOUNT_TESTS_INVALID")
        artifact = context.frozen_admission
        if artifact is not None:
            TInvestFrozenAdmissionEvidenceService.validate(artifact)
            require(context.expected_artifact_sha256 == artifact.artifact_sha256 and
                context.expected_source_bonds_sha256 == artifact.source_bonds_sha256, "SOURCE_HASH_MISMATCH")
            if context.preflight is not None:
                TInvestBondImportPreflightService.validate_frozen_preflight(context.preflight, artifact.admission_manifest)
            require({a.uid for a in accounts} <= {r.uid for r in artifact.source_bonds}, "ACCOUNT_UID_UNKNOWN")
        else:
            require(context.preflight is None and not accounts and context.expected_artifact_sha256 is None and
                context.expected_source_bonds_sha256 is None, "SOURCE_ENVELOPE_INCONSISTENT")
        context = context.model_copy(update={"accounts": tuple(sorted((a.model_copy(update={
            "passed_tests": tuple(sorted(a.passed_tests))}) for a in accounts), key=lambda a:a.uid))})
    plans = request.reviewed_genesis
    require(len({p.capital_rub for p in plans}) == len(plans) and
        all(p.capital_rub in CAPITAL_GRID_RUB for p in plans), "GENESIS_CAPITAL_INVALID")
    return request.model_copy(update={"bond_ids": tuple(sorted(request.bond_ids)), "source_context": context,
        "reviewed_genesis": tuple(sorted(plans, key=lambda p:p.capital_rub))})


def observation_reason(observed, request):
    if observed is None:
        return "SOURCE_OBSERVATION_TIME_MISSING"
    age = request.evaluation_at - observed
    if age < timedelta(0):
        return "SOURCE_OBSERVATION_IN_FUTURE"
    if age > timedelta(seconds=request.source_max_age_seconds):
        return "SOURCE_STALE"
    return None


def source_findings(request, canonical):
    context = request.source_context
    artifact = context.frozen_admission if context else None
    if artifact is None:
        return ()
    bridges = {r.source_uid:r for r in artifact.identity_bridge.bridge_rows}
    admissions = {r.source_uid:r for r in artifact.admission_manifest.uid_admissions}
    accounts = {a.uid:a for a in context.accounts}
    by_id = {r.bond_id:r for r in canonical}
    requested_isins = {r.isin for r in artifact.internal_bonds if r.bond_id in request.bond_ids and r.isin}
    requested_isins.update(r.isin for r in canonical if r.bond_id in request.bond_ids and r.isin)
    result = []
    for source in sorted(artifact.source_bonds, key=lambda r:r.uid):
        bridge = bridges[source.uid]
        if bridge.bond_id not in request.bond_ids and source.isin not in requested_isins:
            continue
        reasons = []
        admission = admissions[source.uid]
        current = by_id.get(bridge.bond_id)
        identity = UNAVAILABLE
        if bridge.match_state.value == "MATCHED_EXACT_ISIN":
            identity = VERIFIED if current and (current.isin, current.secid) == (bridge.bond_isin, bridge.bond_secid) else CONFLICT if current else UNAVAILABLE
        elif "CONFLICT" in bridge.match_state.value:
            identity = CONFLICT
        if identity != VERIFIED:
            reasons.append("CANONICAL_IDENTITY_CONFLICT" if identity == CONFLICT else "EXACT_SOURCE_BINDING_UNAVAILABLE")
        temporal = observation_reason(context.observed_at, request)
        if temporal:
            reasons.append(temporal)
        availability = UNKNOWN if source.availability_classification is None else VERIFIED
        if source.availability_classification is None:
            reasons.append("BUY_AVAILABILITY_UNKNOWN")
        elif source.availability_classification.value != "API_BUY_AVAILABLE":
            reasons.append("API_NOT_BUYABLE")
        account_state = VERIFIED
        if source.for_qual_investor is True:
            account_state = UNAVAILABLE; reasons.append("QUALIFIED_ONLY_RESTRICTED")
        elif source.for_qual_investor is None:
            account_state = UNKNOWN; reasons.append("QUALIFICATION_UNKNOWN")
        account = accounts.get(source.uid)
        if account:
            age_reason = observation_reason(account.observed_at, request)
            if age_reason:
                account_state = UNKNOWN; reasons.append("ACCOUNT_"+age_reason)
            elif account.permitted is False:
                account_state = UNAVAILABLE; reasons.append("ACCOUNT_RESTRICTED")
            elif account.permitted is None:
                account_state = UNKNOWN; reasons.append("ACCOUNT_PERMISSION_UNKNOWN")
        if source.required_tests_state != "SOURCE_EMPTY":
            if (source.required_tests_state != "SOURCE_VALUES" or not account or account.permitted is not True or
                    observation_reason(account.observed_at, request) or not set(source.required_tests or ()) <= set(account.passed_tests)):
                if account_state == VERIFIED: account_state = UNKNOWN
                reasons.append("REQUIRED_TESTS_NOT_PROVEN")
        if source.class_code != "TQCB":
            reasons.append("SOURCE_BOARD_UNKNOWN" if source.class_code is None else "SOURCE_BOARD_NOT_TQCB")
        # Raw activity remains a separate diagnostic, not MOEX confirmation.
        # Fresh broker trading AND buying are the independent positive authority;
        # explicit inactive evidence always vetoes it, never the reverse.
        raw_status = source.source_fields.get("status")
        raw_traded = source.source_fields.get("is_traded")
        traded = _parse_bool(raw_traded)
        invalid_activity = (raw_traded is not None and traded is None) or (raw_status is not None and type(raw_status) is not str)
        status_inactive = (
            type(raw_status) is str and raw_status.strip().lower() in _INACTIVE_STATUS_VALUES)
        inactive = traded is False or status_inactive
        activity_conflict = traded is True and status_inactive
        active_state = CONFLICT if activity_conflict else UNAVAILABLE if inactive else VERIFIED if traded is True else UNKNOWN
        positive_broker = source.api_trade_available is True and source.buy_available is True
        tradability = UNKNOWN
        basis = "NONE"
        if activity_conflict or (positive_broker and inactive):
            tradability = CONFLICT; basis = "SOURCE_CONFLICT"
            reasons.append("TRADABILITY_SOURCE_CONFLICT")
        elif inactive:
            tradability = UNAVAILABLE; basis = "EXPLICIT_INACTIVE_VETO"
        elif invalid_activity:
            reasons.append("ACTIVITY_SOURCE_VALUE_INVALID")
        elif positive_broker and temporal is None:
            tradability = VERIFIED; basis = "FRESH_BROKER_API_POSITIVE"
        elif source.availability_classification is not None and not positive_broker:
            tradability = UNAVAILABLE
        if inactive: reasons.append("NOT_ACTIVE_OR_NOT_TRADED")
        if admission.admission_state.value != "EXISTING_MATCHED":
            reasons.append("ADMISSION_NOT_EXISTING_MATCHED")
        aggregate = next((r for r in artifact.admission_manifest.existing_bond_aggregates if r.bond_id == bridge.bond_id), None)
        if aggregate is not None and not aggregate.current_pipeline_compatible:
            reasons.append("ADMISSION_PIPELINE_NOT_COMPATIBLE")
        result.append(s.SourceFinding(uid=source.uid, bond_id=bridge.bond_id, figi=source.figi,
            isin=source.isin, secid=bridge.bond_secid, currency=source.currency, class_code=source.class_code, lot=source.lot,
            admission_status=admission.admission_state.value, canonical_identity_status=identity,
            availability_status=availability, account_status=account_state, source_observed_at=context.observed_at,
            buy_available=source.buy_available, api_trade_available=source.api_trade_available,
            for_qual_investor=source.for_qual_investor, required_tests=source.required_tests,
            required_tests_state=source.required_tests_state, active_status=active_state,
            tradability_status=tradability, tradability_basis=basis,
            source_eligible=tradability == VERIFIED and not reasons, reason_codes=tuple(sorted(set(reasons)))))
    return tuple(result)


def stage(name, inputs, passing=(), unknown=(), *, capital=None, reasons=(), unit="BOND_ID", evaluated=True):
    ordering = (lambda v:int(v)) if unit == "BOND_ID" else (lambda v:v)
    inputs = tuple(sorted(map(str, inputs), key=ordering))
    passing = tuple(sorted(map(str, passing), key=ordering))
    unknown = tuple(sorted(map(str, unknown), key=ordering))
    excluded = tuple(v for v in inputs if v not in set(passing) | set(unknown))
    return s.Stage(name=name, unit=unit, capital_rub=capital, evaluation_status="EVALUATED" if evaluated else "NOT_EVALUATED", input_ids=inputs, passing_ids=passing,
        excluded_ids=excluded, unknown_ids=unknown, input_count=len(inputs), passing_count=len(passing),
        excluded_count=len(excluded), unknown_count=len(unknown), reason_codes=tuple(sorted(set(reasons))))


@dataclass(frozen=True)
class CaseWork:
    capital: Decimal
    strategy: object = None
    execution: object = None
    experiment: object = None
    errors: tuple[str, ...] = ()


def case_finding(work, findings, investment, sources=()):
    strategy, execution, experiment = work.strategy, work.execution, work.experiment
    benchmark = s.BenchmarkFinding()
    errors = list(work.errors)
    if experiment:
        b = experiment.benchmark
        benchmark = s.BenchmarkFinding(status=experiment.status, common_market_trade_date=experiment.common_market_trade_date,
            representatives=tuple(s.Representative(bond_id=r.bond_id, snapshot_id=r.snapshot_id,
                modified_duration=r.modified_duration_years) for r in b.representatives) if b else (),
            selected_bond_ids=tuple(c.representative.bond_id for c in b.components) if b else (),
            matching_mode=b.matching_mode if b else None, benchmark_genesis_sha256=experiment.benchmark_genesis_sha256,
            blockers=experiment.blockers)
    positions = []
    if execution:
        for p in execution.positions:
            source = next(f for f in findings if f.bond_id == p.bond_id)
            if not source.eligible_witness_uids: errors.append("SELECTED_SOURCE_ELIGIBILITY_UNPROVEN")
            lots = tuple(r.lot for r in sources if r.bond_id == p.bond_id and r.class_code == "TQCB" and r.lot is not None)
            if any(type(lot) is not int or lot <= 0 for lot in lots): errors.append("SOURCE_LOT_INVALID")
            elif lots and (len(set(lots)) != 1 or lots[0] != p.lot_size): errors.append("SOURCE_LOT_TERMS_CONFLICT")
            with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
                below = (strategy.request.capital_rub*strategy.policy.min_initial_position_weight < p.lot_dirty_value_rub
                    if p.lot_dirty_value_rub is not None else None)
            positions.append(s.PositionFinding(bond_id=p.bond_id, target_weight=p.target_weight,
                target_amount_rub=p.target_amount_rub, terms_status=p.terms_status, lot_size=p.lot_size,
                trading_board=p.trading_board, dirty_value=p.dirty_value_currency, lot_dirty_value=p.lot_dirty_value_rub,
                planned_lots=p.planned_lot_count, planned_quantity=p.planned_bond_quantity,
                cash_cost_rub=p.planned_cash_cost_rub, residual_target_rub=p.target_shortfall_rub,
                realized_weight=p.realized_shadow_weight, market_snapshot_id=p.market_snapshot_id,
                security_master_profile_id=p.security_master_profile_id, status=p.status,
                seed_below_one_lot=below, blockers=p.terms_blockers))
        errors.extend(execution.quality_flags)
        errors.extend(execution.post_rounding_risk_evaluation.reasons)
        if execution.summary.planned_position_count == 0: errors.append("NO_POSITIVE_INTEGER_LOTS")
        if any(p.trading_board != "TQCB" for p in execution.positions): errors.append("GENESIS_BOARD_NOT_TQCB")
    if benchmark.status != "READY": errors.extend(benchmark.blockers or ("BENCHMARK_NOT_READY",))
    if execution is None: status = "NOT_EVALUATED"
    elif strategy.status == "EMPTY": status = "EMPTY"
    elif execution.status == "UNEXECUTABLE": status = "UNEXECUTABLE"
    elif errors or execution.status != "READY": status = "BLOCKED"
    else: status = "READY"
    return s.CapitalCase(capital_rub=work.capital, status=status,
        strategy_status=strategy.status if strategy else None, execution_status=execution.status if execution else None,
        post_rounding_risk_status=execution.post_rounding_risk_evaluation.status if execution else None,
        strategy_summary=strategy.summary if strategy else None, execution_summary=execution.summary if execution else None,
        risk_metrics=execution.post_rounding_risk_evaluation.metrics if execution else None,
        issuer_count=execution.post_rounding_risk_evaluation.metrics.issuer_count if execution else None,
        selected_bond_ids=tuple(p.bond_id for p in strategy.positions) if strategy else (),
        strategy_targets=tuple(s.StrategyTarget(bond_id=p.bond_id,legal_issuer_id=p.legal_issuer_id,
            target_weight=p.target_weight,target_amount_rub=p.target_amount_rub) for p in strategy.positions) if strategy else (),
        positions=tuple(positions), benchmark=benchmark,
        source_evidence_sha256=fingerprint(investment) if investment else None,
        plan_sha256=native_fingerprint(execution) if execution else None, blockers=tuple(sorted(set(errors))))


def signed(report):
    return report.model_copy(update={"report_sha256": digest(report, exclude=("report_sha256",))})


def blocked(code, request=None):
    return signed(s.CurrentForwardReadinessReport(status="BLOCKED",
        as_of_date=request.as_of_date if request else None, evaluation_at=request.evaluation_at if request else None,
        implementation_sha=request.implementation_sha if request else None,
        input_fingerprints=(s.Fingerprint(name="request", sha256=fingerprint(request)),) if request else (),
        capital_cases=tuple(s.CapitalCase(capital_rub=c, blockers=(code,)) for c in CAPITAL_GRID_RUB),
        readiness_decisions=tuple(s.ReadinessDecision(name=n, value="UNKNOWN", reason_codes=(code,)) for n in DECISIONS),
        blockers=(code,)))


def build_report(request, canonical, investment, risk, work, *, errors=()):
    require(tuple(w.capital for w in work) == CAPITAL_GRID_RUB, "CAPITAL_CASE_ORDER_CONFLICT")
    sources = source_findings(request, canonical)
    existing = {p.bond_id:p for p in canonical if p.bond_id in request.bond_ids}
    batch = investment.source_batch if investment else None
    evaluations = {e.bond_id:e for e in investment.evaluations} if investment else {}
    risks = {r.bond_id:r for r in risk.candidates} if risk else {}
    exclusions = {e.bond_id:e for e in batch.exclusions} if batch else {}
    findings = []
    for i in request.bond_ids:
        e, r, excluded = evaluations.get(i), risks.get(i), exclusions.get(i)
        rows = [s for s in sources if s.bond_id == i]
        reasons = set(r.reasons if r else ()) | set(excluded.reasons if excluded else ())
        reasons.update(reason for row in rows for reason in row.reason_codes)
        if not rows: reasons.add("SOURCE_BINDING_MISSING")
        if i not in existing: reasons.add("CANONICAL_BOND_MISSING")
        if excluded: reasons.add("UPSTREAM_EXCLUSION_CHILD_PROVENANCE_NOT_EXPOSED")
        if e and e.status != "READY": reasons.add("CREDIT_PEER_CONTEXT_UNAVAILABLE")
        f, p = (e.candidate.features, e.candidate.provenance) if e else (None, None)
        findings.append(s.CandidateFinding(bond_id=i, isin=existing[i].isin if i in existing else None,
            secid=existing[i].secid if i in existing else None, source_uids=tuple(row.uid for row in rows),
            eligible_witness_uids=tuple(row.uid for row in rows if row.source_eligible), core_ready=e is not None,
            investment_status=e.status if e else None, rank=e.rank if e else None,
            investment_score=e.investment_score_v1 if e else None, risk_status=r.status if r else None,
            legal_issuer_id=r.legal_issuer_id if r else None, issuer_profile_id=p.bond_legal_issuer_profile_id if p else None,
            security_master_profile_id=p.security_master_profile_id if p else None,
            market_snapshot_id=p.market_snapshot_id if p else None, market_trade_date=p.market_trade_date if p else None,
            curve_trade_date=p.curve_trade_date if p else None, nominal_value=f.nominal_value if f else None,
            currency=f.currency_code if f else None, liquidity_score=f.liquidity_score_v1 if f else None,
            median_daily_turnover=f.median_daily_turnover_value if f else None,
            modified_duration=f.modified_duration_years if f else None,
            relative_dv01=f.relative_price_sensitivity_per_1bp if f else None,
            ready_peer_context_count=len(e.ready_credit_contexts) if e else None,
            reason_codes=tuple(sorted(reasons)), source_evaluation_sha256=fingerprint(e) if e else None))
    cases = tuple(case_finding(w, findings, investment, sources) for w in work)
    bound = {row.bond_id for row in sources if row.canonical_identity_status == VERIFIED} & set(existing)
    eligible_sources = {f.bond_id for f in findings if f.eligible_witness_uids}
    known_source_rejections = {"API_NOT_BUYABLE", "QUALIFIED_ONLY_RESTRICTED", "ACCOUNT_RESTRICTED",
        "SOURCE_BOARD_NOT_TQCB", "NOT_ACTIVE_OR_NOT_TRADED", "ADMISSION_NOT_EXISTING_MATCHED",
        "CANONICAL_IDENTITY_CONFLICT", "TRADABILITY_SOURCE_CONFLICT"}
    source_unknown = {f.bond_id for f in findings if not f.eligible_witness_uids and
        (not any(r.bond_id == f.bond_id for r in sources) or any(r.bond_id == f.bond_id and
            not set(r.reason_codes) & known_source_rejections for r in sources))}
    core = set(batch.candidate_bond_ids) if batch else set()
    inv_ready = {i for i,e in evaluations.items() if e.status == "READY"}
    risk_ready = {i for i,r in risks.items() if r.status == "ELIGIBLE"}
    source_reasons = {reason for row in sources for reason in row.reason_codes}
    source_supplied = bool(request.source_context and request.source_context.frozen_admission)
    stages = [stage("EXPLICIT_SCOPE_SOURCE_UNIVERSE", (r.uid for r in sources), (r.uid for r in sources), unit="UID",
            evaluated=source_supplied, reasons=() if source_supplied else ("FROZEN_SOURCE_EVIDENCE_MISSING",)),
        stage("EXACT_IDENTITY_BOUND", request.bond_ids, bound,
            set(request.bond_ids)-bound if not request.source_context or not request.source_context.frozen_admission else (),
            reasons=source_reasons | ({"SOURCE_BINDING_MISSING"} if bound != set(request.bond_ids) else set())),
        stage("CANONICAL_BOND_PRESENT", request.bond_ids, set(existing), reasons=("CANONICAL_BOND_MISSING",) if len(existing)!=len(request.bond_ids) else ()),
        stage("CORE_M3_READY", request.bond_ids, core, request.bond_ids if batch is None else (),
            reasons={reason for row in exclusions.values() for reason in row.reasons} | (set(errors) if batch is None else set())),
        stage("SOURCE_ELIGIBLE_CORE_INTERSECTION", core, core & eligible_sources,
            core & source_unknown, reasons=source_reasons),
        stage("INVESTMENT_READY", core, inv_ready, reasons=("CREDIT_PEER_CONTEXT_UNAVAILABLE",) if core-inv_ready else ()),
        stage("RISK_ELIGIBLE", inv_ready, risk_ready & inv_ready,
            inv_ready if risk is None else (), reasons={reason for row in risks.values() for reason in row.reasons} | (set(errors) if risk is None else set()))]
    for case in cases:
        selected = set(case.selected_bond_ids)
        kwargs = dict(capital=case.capital_rub)
        stages.append(stage("STRATEGY_SELECTED", risk_ready, selected, risk_ready if case.strategy_status is None else (),
            reasons=("NOT_SELECTED_BY_NATIVE_STRATEGY",) if risk_ready-selected else (), **kwargs))
        terms = {p.bond_id for p in case.positions if p.terms_status == "READY"}
        positive = {p.bond_id for p in case.positions if p.status == "PLANNED"}
        stages.extend((stage("SHADOW_TERMS_VERIFIED", selected, terms, selected if case.execution_status is None else (),
                reasons={reason for p in case.positions for reason in p.blockers} | (set(case.blockers) if case.execution_status is None else set()), **kwargs),
            stage("POSITIVE_INTEGER_LOTS", terms, positive, reasons=("TARGET_BELOW_ONE_LOT",) if terms-positive else (), **kwargs),
            stage("POST_ROUNDING_RISK_PASS", positive, positive if case.post_rounding_risk_status == "PASS" else (),
                reasons=case.blockers if case.post_rounding_risk_status != "PASS" else (), **kwargs)))
    family_counts = []
    if batch:
        valid_ids = {e.bond_id for e in batch.exclusions if e.m3_status == "CONSISTENT"}
        for flag, reason in CORE_REASONS:
            present = core | {i for i in valid_ids if reason not in exclusions[i].reasons}
            family_counts.append(s.Count(name=flag, count=len(present)))
    global_errors = set(errors)
    global_errors.update(reason for row in sources for reason in row.reason_codes if "CONFLICT" in reason)
    if family_counts and all(c.count > 0 for c in family_counts) and not core:
        global_errors.add("INDIVIDUAL_FAMILIES_AVAILABLE_BUT_NO_JOINT_READY_CANDIDATES")
    context = request.source_context
    artifact = context.frozen_admission if context else None
    if artifact is None: global_errors.add("FROZEN_SOURCE_EVIDENCE_MISSING")
    elif observation_reason(context.observed_at, request): global_errors.add(observation_reason(context.observed_at, request))
    if artifact is not None and not artifact.acquisition_complete:
        global_errors.add("SOURCE_ACQUISITION_INCOMPLETE")
    global_errors.update(reason for case in cases for reason in case.blockers)
    real = cases[0]
    def yn(condition, evaluated=True): return "YES" if condition else "NO" if evaluated else "UNKNOWN"
    evaluated = all(c.execution_status is not None for c in cases)
    all_ready = all(c.status == "READY" for c in cases) and not global_errors
    choices = (yn(bound == set(request.bond_ids), artifact is not None), yn(bool(core), batch is not None),
        yn(bool(inv_ready), investment is not None), yn(bool(risk_ready), risk is not None),
        yn(bool(real.positions), real.strategy_status is not None),
        yn(real.execution_status == "READY" and bool(real.positions) and all(next(f for f in findings if f.bond_id == p.bond_id).eligible_witness_uids for p in real.positions), real.execution_status is not None),
        yn(real.post_rounding_risk_status == "PASS" and bool(real.execution_summary and real.execution_summary.planned_position_count), real.execution_status is not None),
        yn(evaluated), yn(all_ready, evaluated),
        yn(all(c.benchmark.status == "READY" for c in cases), all(c.benchmark.status != "NOT_EVALUATED" for c in cases)),
        "UNKNOWN", yn(all_ready, evaluated))
    decision_reasons = (source_reasons | ({"SOURCE_BINDING_MISSING"} if bound != set(request.bond_ids) else set()),
        {reason for row in exclusions.values() for reason in row.reasons} or {"CORE_NOT_EVALUATED"},
        {"CREDIT_PEER_CONTEXT_OR_CORE_UNAVAILABLE"}, {reason for row in risks.values() for reason in row.reasons} or {"RISK_NOT_EVALUATED"},
        set(real.blockers) | ({"EMPTY_SELECTION"} if not real.selected_bond_ids else set()), set(real.blockers), set(real.blockers),
        global_errors or {"CASE_NOT_EVALUATED"}, global_errors,
        {reason for case in cases for reason in case.benchmark.blockers},
        {"REAL_CURRENT_OPERATIONAL_EVIDENCE_NOT_VERIFIED"}, global_errors)
    decisions = tuple(s.ReadinessDecision(name=n, value=v, reason_codes=tuple(sorted(reasons)) if v != "YES" else ())
        for n,v,reasons in zip(DECISIONS, choices, decision_reasons))
    reasons_by_bond = {}
    for f in findings:
        for reason in f.reason_codes: reasons_by_bond.setdefault(reason, set()).add(f.bond_id)
    for reason in global_errors: reasons_by_bond.setdefault(reason, set())
    remediation = []
    for reason, ids in sorted(reasons_by_bond.items()):
        policy = any(word in reason for word in ("LIMIT", "BELOW_MINIMUM", "API_NOT_BUYABLE", "QUALIFIED_ONLY", "ACCOUNT_RESTRICTED",
            "TARGET_BELOW_ONE_LOT", "NO_POSITIVE_INTEGER_LOTS"))
        authority = "AUTHORITY" in reason
        family = ("ACCOUNT_ACCESS" if any(w in reason for w in ("ACCOUNT", "TESTS", "QUALIFICATION", "QUALIFIED")) else
            "TASK303_GENESIS_TASK304_BENCHMARK" if any(w in reason for w in ("GENESIS", "BENCHMARK")) else
            "ISSUER" if "ISSUER" in reason else "EXECUTION_TERMS" if any(w in reason for w in ("LOT", "BOARD", "PROFILE")) else
            "TINVEST_CURRENT_UNIVERSE" if any(w in reason for w in ("SOURCE", "BUY", "FROZEN")) else "MARKET_CREDIT_SOURCE")
        remediation.append(s.Remediation(reason_code=reason, affected_bond_ids=tuple(sorted(ids)), source_family=family,
            classification="POLICY_BLOCKED_AS_DESIGNED" if policy else "NO_SOURCE_AUTHORITY_AVAILABLE" if authority else "REPAIRABLE_INPUT_COVERAGE_GAP",
            missing_or_conflicting_evidence=reason, dependency="Independent review of exact affected input evidence",
            minimum_safe_remediation="Preserve frozen policy; review exclusion" if policy else "Supply bounded exact source-backed evidence; no automatic repair"))
    status = "READY" if all_ready and not global_errors else "SOURCE_CONFLICT" if any("CONFLICT" in c for c in global_errors) else "SOURCE_STALE" if "SOURCE_STALE" in global_errors else "INPUTS_MISSING" if artifact is None or investment is None else "BLOCKED"
    return signed(s.CurrentForwardReadinessReport(status=status, as_of_date=request.as_of_date,
        evaluation_at=request.evaluation_at, implementation_sha=request.implementation_sha,
        input_fingerprints=(s.Fingerprint(name="request", sha256=fingerprint(request)),),
        source_collection_id=context.collection_id if context else None, source_observed_at=context.observed_at if context else None,
        source_contract_version="tinvest-current-instrument-universe-v1" if artifact else None,
        admission_manifest_sha256=artifact.admission_manifest.provenance.admission_row_set_sha256 if artifact else None,
        input_counts=tuple(s.Count(name=n,count=c) for n,c in (("requested_bonds",len(request.bond_ids)),
            ("full_source_uids",len(artifact.source_bonds) if artifact else None),("in_scope_source_uids",len(sources) if artifact else None),
            ("core_ready",len(core) if batch else None),("investment_ready",len(inv_ready) if investment else None),
            ("risk_eligible",len(risk_ready) if risk else None))),
        funnel_stages=tuple(stages), source_findings=sources, candidate_findings=tuple(findings), capital_cases=cases,
        independent_family_counts=tuple(family_counts), joint_source_core_bond_ids=tuple(sorted(core & eligible_sources)),
        readiness_decisions=decisions, recommended_remediations=tuple(remediation), blockers=tuple(sorted(global_errors))))
