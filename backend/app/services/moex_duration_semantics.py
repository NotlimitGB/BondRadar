"""Pure MOEX source-day authority, independent of stored snapshot normalization."""

from dataclasses import dataclass
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from typing import Any, Literal

MOEX_DURATION_DIVISOR = Decimal("365")
MOEX_DURATION_MAPPING_NOTE = "MOEX DURATION days divided by 365"
LEGACY_DURATION_MAPPING_NOTE = "DURATION looked like days and was divided by 365"
DurationStatus = Literal[
    "READY", "RAW_DURATION_MISSING", "MALFORMED_RAW_DURATION", "CONFLICTING_RAW_DURATION",
]


@dataclass(frozen=True)
class MoexDurationResult:
    duration_years: Decimal | None
    source_duration_days: Decimal | None
    status: DurationStatus
    quality_flags: tuple[str, ...]


def _source_days(value: Any) -> Decimal | None:
    # Same safe numeric representations and missing semantics as Task267.
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float, str)):
        raise ValueError("Unsupported duration representation")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value).strip().replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError("Malformed duration representation") from exc
    if not number.is_finite() or number < 0:
        raise ValueError("Non-finite or negative duration")
    return number


def normalize_moex_duration(
    raw_payload: Any, *, stored_duration_years: Decimal | None = None,
) -> MoexDurationResult:
    """Caller supplies known MOEX evidence; no interpretation of manual sources."""
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    values: list[Decimal] = []
    flags: set[str] = set()
    for name, keys in (("moex", ("DURATION", "duration")), ("canonical", ("duration",))):
        container = payload.get(name)
        if not isinstance(container, dict):
            continue
        for key in keys:
            if key not in container:
                continue
            try:
                value = _source_days(container[key])
            except ValueError:
                flags.add("MALFORMED_RAW_DURATION")
            else:
                if value is not None:
                    values.append(value)
    if values and any(value != values[0] for value in values[1:]):
        flags.add("CONFLICTING_RAW_DURATION")
    if flags:
        status = "MALFORMED_RAW_DURATION" if "MALFORMED_RAW_DURATION" in flags else "CONFLICTING_RAW_DURATION"
        return MoexDurationResult(None, None, status, tuple(sorted(flags)))
    if not values:
        return MoexDurationResult(None, None, "RAW_DURATION_MISSING", ("DURATION_RAW_MISSING",))
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        years = values[0] / MOEX_DURATION_DIVISOR
    if stored_duration_years is not None:
        if (not isinstance(stored_duration_years, Decimal) or not stored_duration_years.is_finite()
                or stored_duration_years != years):
            flags.add("STORED_DURATION_MISMATCH")
    return MoexDurationResult(years, values[0], "READY", tuple(sorted(flags)))
