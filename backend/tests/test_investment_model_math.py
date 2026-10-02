"""Task299 endpoint midrank policy and Decimal isolation."""

from decimal import Decimal, getcontext, localcontext, ROUND_UP

import pytest

from app.services.investment_model_math import midrank_percentile
from app.schemas.investment_model import InvestmentModelPolicyV1

D = Decimal


@pytest.mark.parametrize("values,expected", [(["1"], ["50"]), (["1", "2"], ["0", "100"]),
    (["1", "1", "3", "3"], ["16.66666666666666666666666667", "16.66666666666666666666666667", "83.33333333333333333333333333", "83.33333333333333333333333333"]),
    (["0", "0", "0"], ["50", "50", "50"]), (["-1", "0", "1"], ["0", "50", "100"])])
def test_midrank(values, expected):
    observations = tuple(map(D, values))
    assert [midrank_percentile(v, observations) for v in observations] == list(map(D, expected))
    assert [midrank_percentile(v, tuple(reversed(observations))) for v in observations] == list(map(D, expected))


@pytest.mark.parametrize("value,values", [(D(1), []), (1, [D(1)]), (D("NaN"), [D(1)]),
    (D(1), [D("Infinity")]), (D(1), [True]), (D(1), iter([D(1)])), (D(2), [D(1)])])
def test_invalid(value, values):
    with pytest.raises(ValueError):
        midrank_percentile(value, values)


def test_policy_and_context():
    with localcontext() as ctx:
        ctx.prec = 3
        ctx.rounding = ROUND_UP
        before = ctx.copy()
        assert midrank_percentile(D(1), (D(1), D(1), D(3), D(3))) == D("16.66666666666666666666666667")
        policy = InvestmentModelPolicyV1()
        assert policy.min_peer_count == 2 and policy.ofz_relative_value_weight == D(".40")
        assert getcontext().prec == before.prec and getcontext().rounding == before.rounding
    for kwargs in ({"ofz_relative_value_weight": D(".5")}, {"duration_weight": D("NaN")}, {"min_peer_count": 1}, {"extra": 1}):
        with pytest.raises(ValueError):
            InvestmentModelPolicyV1(**kwargs)
