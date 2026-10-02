"""Synthetic evidence, multi-cohort policy and exact batch ranking."""

import ast
from copy import deepcopy
from decimal import Decimal, localcontext, ROUND_UP
from pathlib import Path

import pytest

from app.schemas.investment_model import InvestmentEvaluationBatchView
from app.services.investment_model_reducer import InvestmentModelReducer, _rank_ready
from app.services.investment_model_snapshot_service import build_peer_contexts
from app.services.unified_candidate_reducer import UnifiedCandidateReducer
from test_unified_candidate_reducer import composite, snapshot, incomplete

D = Decimal


def batch(n=3, missing=(), changes=None):
    composites = []
    for i in range(1, n+1):
        c = composite(i)
        data = c.model_dump()
        changes_i = (changes or {}).get(i, {})
        for child, field, value in changes_i.get("fields", []):
            data[child][field] = value
        if "value" in changes_i:
            e = data["credit_comparability"]["bond_rating_entries"][0]
            e["rating_value_raw"] = e["cohort_key"]["rating_value_raw"] = changes_i["value"]
        c = type(c).model_validate(data)
        composites.append(c)
    return UnifiedCandidateReducer.build(snapshot(composites, missing))


def evaluate(source):
    return InvestmentModelReducer.build(source, build_peer_contexts(source))


def varied_batch(values):
    rows = []
    for i, replacements in enumerate(values, 1):
        c = composite(i)
        def replace(value):
            if isinstance(value, dict):
                return {key: replacements.get(key, replace(item)) for key, item in value.items()}
            if isinstance(value, list): return [replace(item) for item in value]
            return value
        rows.append(type(c).model_validate(replace(c.model_dump())))
    return UnifiedCandidateReducer.build(snapshot(rows))


def test_ready_exact_ties_policy_serialization_and_source_preservation():
    source = batch(missing=(4,))
    before = deepcopy(source.model_dump())
    result = evaluate(source)
    assert result.candidate_count == result.ready_count == 3 and result.unavailable_count == 0
    assert result.ranked_bond_ids == (1,2,3) and result.normalization_bond_ids == (1,2,3)
    e = result.evaluations[0]
    assert e.ofz_relative_value_score == e.credit_cohort_relative_value_score == e.duration_score == D(50)
    assert e.investment_score_v1 == D(".4")*D(50)+D(".3")*D(50)+D(".2")*e.liquidity_score+D(".1")*D(50)
    assert e.candidate is source.candidates[0] and e.attempted_credit_contexts[0].distribution.eligible_peer_count == 2
    assert e.selected_credit_context.member.cohort_key.rating_value_raw == " AA+(RU) "
    assert source.model_dump() == before
    assert result.model_dump() == evaluate(source).model_dump()
    assert InvestmentEvaluationBatchView.model_validate_json(result.model_dump_json()).model_dump() == result.model_dump()
    with pytest.raises(ValueError):
        result.ready_count = 0
    with pytest.raises(ValueError):
        type(result)(**result.model_dump(), extra=1)
    assert all(value is False for name, value in result.capabilities.model_dump().items() if name not in {"investment_model_ready", "investment_score_ready", "investment_ranking_ready"})


@pytest.mark.parametrize("n", [1,2])
def test_minimum_and_no_partial_score(n):
    result = evaluate(batch(n))
    assert result.ready_count == 0 and result.score_min is result.score_max is None
    for e in result.evaluations:
        assert e.status == "CREDIT_PEER_CONTEXT_UNAVAILABLE" and e.rank is e.investment_score_v1 is None
        assert e.ofz_relative_value_percentile is e.duration_percentile is None
        assert len(e.attempted_credit_contexts) == 1 and not e.ready_credit_contexts


def test_empty_and_exclusions_not_resurrected():
    source = UnifiedCandidateReducer.build(snapshot([], missing=(9,)))
    result = evaluate(source)
    assert result.candidate_count == 0 and result.evaluations == () and result.ranked_bond_ids == ()
    source = UnifiedCandidateReducer.build(snapshot([incomplete(composite(), "dv01_ready")]))
    assert evaluate(source).candidate_count == 0


def test_many_cohorts_conservative_selection_and_attempt_preservation():
    source = batch(4)
    candidates = []
    for c in source.candidates:
        data = c.model_dump()
        e = deepcopy(data["m3"]["credit_comparability"]["bond_rating_entries"][0])
        e["rating_agency"] = e["cohort_key"]["rating_agency"] = "EXPERT_RA"
        e["source_provider"] = e["cohort_key"]["source_provider"] = "Other Provider"
        data["m3"]["credit_comparability"]["bond_rating_entries"].append(e)
        candidates.append(type(c).model_validate(data))
    source = source.model_copy(update={"candidates": tuple(candidates)})
    contexts = build_peer_contexts(source)
    # Supplied validated distributions, not recalculated by Investment Model.
    changed = []
    for ctx in contexts:
        percentile = D("25") if ctx.rating_agency == "EXPERT_RA" else D("75")
        changed.append(ctx.model_copy(update={"distribution": ctx.distribution.model_copy(update={"target_spread_percentile": percentile})}))
    result = InvestmentModelReducer.build(source, tuple(reversed(changed)))
    for e in result.evaluations:
        assert len(e.attempted_credit_contexts) == len(e.ready_credit_contexts) == 2
        assert e.credit_cohort_relative_value_score == D(25)
        assert e.selected_credit_context.rating_agency == "EXPERT_RA"


def test_non_scorable_not_in_normalization_but_in_credit_benchmark():
    source = batch(4, changes={4:{"value":"Different"}})
    result = evaluate(source)
    assert result.normalization_bond_ids == (1,2,3) and result.evaluations[-1].rank is None
    assert all(ctx.distribution.candidate_count == 4 for e in result.evaluations for ctx in e.attempted_credit_contexts)


@pytest.mark.parametrize("field,value", [("candidate_count", True), ("contract_version", "bad"), ("pit_ready", True),
    ("candidate_bond_ids", (1,1,2)), ("status", "PARTIAL"), ("market_source", "MOEX"), ("missing_bond_ids", (8,))])
def test_bad_batch_fails_without_result(field,value):
    source = batch()
    with pytest.raises(ValueError):
        evaluate(source.model_copy(update={field:value}))


@pytest.mark.parametrize("field,value", [("spread_to_ofz_bps", D("NaN")), ("liquidity_score_v1", D(101)),
    ("modified_duration_years", D(-1)), ("nominal_value", D(0)), ("dv01_currency_per_bond", D("Infinity")),
    ("market_snapshot_id", True), ("yield_to_maturity_pct", D(999))])
def test_corrupt_projection(field, value):
    source = batch()
    c = source.candidates[0]
    c = c.model_copy(update={"features":c.features.model_copy(update={field:value})})
    with pytest.raises(ValueError):
        evaluate(source.model_copy(update={"candidates":(c,)+source.candidates[1:]}))


@pytest.mark.parametrize("case", ["missing", "duplicate", "unknown", "pit", "version", "percentile", "peer", "count", "snapshot", "key"])
def test_bad_context(case):
    source = batch()
    contexts = list(build_peer_contexts(source))
    ctx = contexts[0]
    if case == "missing": contexts.pop()
    elif case == "duplicate": contexts.append(ctx)
    elif case == "unknown": contexts[0] = ctx.model_copy(update={"bond_id":99})
    elif case == "pit": contexts[0] = ctx.model_copy(update={"pit_ready":True})
    else:
        d = ctx.distribution
        updates = {"version":{"contract_version":"bad"}, "percentile":{"target_spread_percentile":D(101)},
            "peer":{"eligible_peers":[]}, "count":{"candidate_count":2},
            "snapshot":{"provenance":d.provenance.model_copy(update={"target_market_snapshot_id":999})},
            "key":{"cohort_key":d.cohort_key.model_copy(update={"source_provider":"other"})}}[case]
        contexts[0] = ctx.model_copy(update={"distribution":d.model_copy(update=updates)})
    with pytest.raises(ValueError): InvestmentModelReducer.build(source, contexts)


def test_context_and_input_isolation():
    source = batch()
    contexts = build_peer_contexts(source)
    before = deepcopy([source.model_dump(), [c.model_dump() for c in contexts]])
    expected = InvestmentModelReducer.build(source, contexts).model_dump()
    with localcontext() as ctx:
        ctx.prec = 3; ctx.rounding = ROUND_UP
        assert InvestmentModelReducer.build(source, tuple(reversed(contexts))).model_dump() == expected
        assert ctx.prec == 3 and ctx.rounding == ROUND_UP
    assert [source.model_dump(), [c.model_dump() for c in contexts]] == before


def test_actual_different_spreads_durations_and_exact_weighted_rank():
    source = varied_batch([
        {"spread_to_ofz_bps":D("-100"), "modified_duration_years":D("3"), "liquidity_score_v1":D("0")},
        {"spread_to_ofz_bps":D("0"), "modified_duration_years":D("2"), "liquidity_score_v1":D("50")},
        {"spread_to_ofz_bps":D("100"), "modified_duration_years":D("0"), "liquidity_score_v1":D("100")},
    ])
    result = evaluate(source)
    assert [e.ofz_relative_value_score for e in result.evaluations] == [D(0),D(50),D(100)]
    assert [e.duration_score for e in result.evaluations] == [D(0),D(50),D(100)]
    assert [e.credit_cohort_relative_value_score for e in result.evaluations] == [D(0),D(50),D(100)]
    assert [e.investment_score_v1 for e in result.evaluations] == [D(0),D(50),D(100)]
    assert result.ranked_bond_ids == (3,2,1)
    assert [e.rank for e in result.evaluations] == [3,2,1]
    assert result.evaluations[-1].modified_duration_years == 0


def test_single_scorable_normalization_universe_is_neutral():
    source = batch()
    candidates = list(source.candidates)
    for index in (1,2):
        c = candidates[index]
        credit = c.m3.credit_comparability.model_copy(update={"status":"DEPENDENCY_EVIDENCE_INVALID"})
        candidates[index] = c.model_copy(update={"m3":c.m3.model_copy(update={"credit_comparability":credit})})
    source = source.model_copy(update={"candidates":tuple(candidates)})
    # Globally unavailable comparability does not produce usable peers, so no target is scorable.
    assert evaluate(source).ready_count == 0


@pytest.mark.parametrize("field", ["investment_score_v1", "credit_cohort_relative_value_score",
    "ofz_relative_value_score", "liquidity_score", "duration_score"])
def test_every_rank_tie_break_field(field):
    e = evaluate(batch()).evaluations[0]
    scores = {name:D(50) for name in ("investment_score_v1", "credit_cohort_relative_value_score",
        "ofz_relative_value_score", "liquidity_score", "duration_score")}
    first = e.model_copy(update={**scores,"bond_id":1})
    second = e.model_copy(update={**scores,"bond_id":2,field:D("50.000000000000000001")})
    unavailable = e.model_copy(update={**scores,"bond_id":3,"status":"CREDIT_PEER_CONTEXT_UNAVAILABLE"})
    with localcontext() as ctx:
        ctx.prec=3
        assert [r.bond_id for r in _rank_ready([first,unavailable,second])] == [2,1]
    assert [r.bond_id for r in _rank_ready([first.model_copy(update={"bond_id":2}),first])] == [1,2]


@pytest.mark.parametrize("kind", ["BOND","LEGAL_ISSUER"])
def test_target_family_and_exact_provider_scale_boundaries(kind):
    source = batch(4)
    candidates = []
    for c in source.candidates:
        data = c.model_dump()
        family = "bond_rating_entries" if kind == "BOND" else "issuer_rating_entries"
        entry = deepcopy(data["m3"]["credit_comparability"]["bond_rating_entries"][0])
        entry["target_kind"] = entry["cohort_key"]["target_kind"] = kind
        entry["rating_scale_raw"] = entry["cohort_key"]["rating_scale_raw"] = None if c.bond_id < 4 else ""
        data["m3"]["credit_comparability"][family] = [entry]
        candidates.append(type(c).model_validate(data))
    source = source.model_copy(update={"candidates":tuple(candidates)})
    result = evaluate(source)
    assert all(any(ctx.target_kind == kind for ctx in e.attempted_credit_contexts) for e in result.evaluations)
    assert all(any(ctx.distribution.status == "READY" for ctx in e.attempted_credit_contexts if ctx.target_kind == kind) for e in result.evaluations[:3])
    assert any(ctx.distribution.status == "NO_ELIGIBLE_PEERS" for ctx in result.evaluations[-1].attempted_credit_contexts if ctx.target_kind == kind)


def test_one_unavailable_and_one_ready_context():
    source = batch()
    candidates = []
    for c in source.candidates:
        data = c.model_dump()
        entry = deepcopy(data["m3"]["credit_comparability"]["bond_rating_entries"][0])
        entry["rating_agency"] = entry["cohort_key"]["rating_agency"] = "EXPERT_RA"
        entry["rating_value_raw"] = entry["cohort_key"]["rating_value_raw"] = str(c.bond_id)
        data["m3"]["credit_comparability"]["bond_rating_entries"].append(entry)
        candidates.append(type(c).model_validate(data))
    result = evaluate(source.model_copy(update={"candidates":tuple(candidates)}))
    assert result.ready_count == 3
    assert all(len(e.attempted_credit_contexts)==2 and len(e.ready_credit_contexts)==1 for e in result.evaluations)


@pytest.mark.parametrize("case", ["child_version","child_pit","snapshot","child_identity","provenance","duplicate_peers","status","availability"])
def test_nested_contradictions(case):
    source = batch()
    contexts = list(build_peer_contexts(source))
    if case in {"duplicate_peers","status","availability"}:
        ctx = contexts[0]; d = ctx.distribution
        updates = {"duplicate_peers":{"eligible_peers":[d.eligible_peers[0]]*2},
            "status":{"status":"INSUFFICIENT_PEERS"},
            "availability":{"availability":d.availability.model_copy(update={"has_ready_peer_distribution":False})}}[case]
        contexts[0] = ctx.model_copy(update={"distribution":d.model_copy(update=updates)})
    else:
        c = source.candidates[0]; m = c.m3.market
        updates = {"child_version":{"contract_version":"bad"}, "child_pit":{"pit_ready":True},
            "snapshot":{"market_snapshot_id":99}, "child_identity":{"bond_id":2},
            "provenance":{"provenance":m.provenance.model_copy(update={"market_snapshot_id":99})}}[case]
        c = c.model_copy(update={"m3":c.m3.model_copy(update={"market":m.model_copy(update=updates)})})
        source = source.model_copy(update={"candidates":(c,)+source.candidates[1:]})
    with pytest.raises(ValueError): InvestmentModelReducer.build(source, contexts)


def test_static_pure_boundary():
    for filename in ("investment_model_math.py", "investment_model_reducer.py"):
        tree = ast.parse((Path(__file__).parents[1]/"app"/"services"/filename).read_text())
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(any(word in module for word in ("sqlalchemy", "models", "http", "os", "pathlib")) for module in imports)
        assert not any("app.services" in module and module != "app.services.investment_model_math" for module in imports)
        assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"commit", "flush", "execute", "request", "sync"} for node in ast.walk(tree))
