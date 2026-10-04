"""Exact Task304 preservation, evidence completeness and Decimal profit diagnostics."""
import ast
from copy import deepcopy
from decimal import Decimal, localcontext, ROUND_UP
from pathlib import Path
import pytest
from test_shadow_scale_experiment_genesis import prepared
from test_shadow_experiment_evaluator import observations
from app.schemas.shadow_scale_experiment import ShadowScaleExperimentComparisonViewV1
from app.services.shadow_scale_experiment_evaluator import ShadowScaleExperimentEvaluator
from app.services.shadow_experiment_evaluator import ShadowExperimentEvaluator, signed

D=Decimal


def comparisons(matrix, days=90, unavailable=None):
    results=[]
    for index,case in enumerate(matrix.cases):
        absolute=D(0) if days==0 else D(".04") if index<2 else D("-.01")
        o,b=observations(case.experiment_genesis,days,absolute,D(0) if days==0 else D(".02"))
        if index==unavailable:
            o=o.model_copy(update={"status":"UNAVAILABLE","snapshot":None})
        results.append(ShadowExperimentEvaluator.build(experiment_genesis=case.experiment_genesis,
            strategy_observation=o,benchmark_daily=b))
    return tuple(results)


@pytest.fixture(scope="module")
def baseline_comparisons(prepared):
    return comparisons(prepared.matrix)


@pytest.mark.parametrize("days,status",[(0,"IN_PROGRESS"),(89,"IN_PROGRESS"),(90,"COMPLETE")])
def test_authority_profit_ties_and_roundtrip(prepared,days,status):
    supplied=comparisons(prepared.matrix,days)
    before=deepcopy(tuple(c.model_dump() for c in supplied))
    with localcontext() as ctx:
        ctx.prec=4;ctx.rounding=ROUND_UP
        result=ShadowScaleExperimentEvaluator.build(scale_genesis=prepared.matrix,comparisons=supplied[::-1])
        assert ctx.prec==4 and ctx.rounding==ROUND_UP
    assert result.status==status and result.ready_case_count==7
    expected=prepared.matrix.capital_grid_rub if days==0 else (D(50000),D(100000))
    assert result.highest_strategy_return_capitals_rub==result.highest_excess_return_capitals_rub==expected
    by_capital={c.initial_capital_rub:c for c in supplied}
    for row in result.rows:
        assert row.comparison is by_capital[row.capital_rub]
        assert row.verdict==row.comparison.verdict
        assert row.strategy_profit_rub==row.capital_rub*row.strategy_return
        assert row.benchmark_profit_rub==row.capital_rub*row.benchmark_return
        assert row.excess_profit_rub==row.capital_rub*row.excess_return
    assert before==tuple(c.model_dump() for c in supplied)
    assert ShadowScaleExperimentComparisonViewV1.model_validate_json(result.model_dump_json())==result
    assert "verdict" not in type(result).model_fields
    with pytest.raises(ValueError): result.as_of_date=None
    with pytest.raises(ValueError): type(result)(**result.model_dump(),unexpected=True)


@pytest.mark.parametrize("days",[12,90])
def test_unavailable_never_dropped_or_cherry_picked(prepared,days):
    supplied=comparisons(prepared.matrix,days,unavailable=6)
    result=ShadowScaleExperimentEvaluator.build(scale_genesis=prepared.matrix,comparisons=supplied)
    assert result.status=="INDETERMINATE" and result.unavailable_case_count==1 and len(result.rows)==7
    assert result.highest_strategy_return_capitals_rub is result.highest_excess_return_capitals_rub is None
    row=result.rows[-1]
    assert row.strategy_profit_rub is row.benchmark_profit_rub is row.excess_profit_rub is None
    assert row.comparison is supplied[-1]
    assert row.verdict==("INDETERMINATE" if days==90 else "IN_PROGRESS")


@pytest.mark.parametrize("change",["missing","duplicate","hash","return","date","capital","binding","pit","matrix","case_metric","blocked"])
def test_malformed_evidence_no_partial_result(prepared,baseline_comparisons,change):
    values=list(baseline_comparisons); matrix=prepared.matrix
    c=values[0]
    if change=="missing": values.pop()
    elif change=="duplicate": values[1]=c
    elif change=="hash": values[0]=c.model_copy(update={"comparison_sha256":"0"*64})
    elif change=="return": values[0]=signed(c.model_copy(update={"strategy_return":D(".99")}),"comparison_sha256")
    elif change=="date": values[0]=c.model_copy(update={"as_of_date":matrix.genesis_date})
    elif change=="capital": values[0]=c.model_copy(update={"initial_capital_rub":50000})
    elif change=="binding": values[0]=signed(c.model_copy(update={"experiment_genesis_sha256":"0"*64}),"comparison_sha256")
    elif change=="pit": values[0]=c.model_copy(update={"pit_ready":True})
    elif change=="matrix": matrix=matrix.model_copy(update={"scale_genesis_sha256":"0"*64})
    elif change=="blocked": matrix=signed(matrix.model_copy(update={"status":"BLOCKED"}),"scale_genesis_sha256")
    else:
        case=signed(matrix.cases[0].model_copy(update={"shadow_residual_cash_rub":D(999)}),"case_sha256")
        matrix=signed(matrix.model_copy(update={"cases":(case,*matrix.cases[1:])}),"scale_genesis_sha256")
    with pytest.raises(ValueError): ShadowScaleExperimentEvaluator.build(scale_genesis=matrix,comparisons=tuple(values))


def test_static_no_loader_or_return_formula():
    import app.services.shadow_scale_experiment_evaluator as module
    tree=ast.parse(Path(module.__file__).read_text())
    calls={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
    assert not calls & {"execute","commit","flush","apply","sync","build_daily","build_genesis","open"}
    assert not any(isinstance(n,ast.BinOp) and isinstance(n.op,ast.Div) for n in ast.walk(tree))
