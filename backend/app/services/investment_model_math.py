"""Decimal-only batch normalization; independent of peer-distribution mathematics."""

from collections.abc import Sequence
from decimal import Decimal, Context, ROUND_HALF_EVEN, localcontext


def midrank_percentile(value: Decimal, values: Sequence[Decimal]) -> Decimal:
    if (type(value) is not Decimal or not value.is_finite() or
            not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray))):
        raise ValueError("Finite Decimal observations required")
    observations = tuple(values)
    if not observations or any(type(v) is not Decimal or not v.is_finite() for v in observations):
        raise ValueError("Finite Decimal observations required")
    if value not in observations:
        raise ValueError("Observation must belong to the normalization universe")
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        if len(observations) == 1:
            return Decimal("50")
        lower = sum(v < value for v in observations)
        equal = sum(v == value for v in observations)
        return Decimal("100") * (Decimal(lower) + Decimal(equal - 1) / Decimal("2")) / Decimal(len(observations) - 1)
