from __future__ import annotations

import re
from typing import Any


_CURRENCY_CODE = re.compile(r"[A-Z]{3}")


def canonicalize_moex_currency(value: Any) -> str | None:
    if value is None:
        return None
    currency = str(value).strip().upper()
    if not _CURRENCY_CODE.fullmatch(currency):
        return None
    if currency == "SUR":
        return "RUB"
    return currency
