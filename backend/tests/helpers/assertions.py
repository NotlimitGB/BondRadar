import json
import re
from typing import Any


FORBIDDEN_INVESTMENT_ACTION_PATTERNS = [
    r"\bbuy\b",
    r"\bsell\b",
    r"\bhold\b",
    r"\bstrong_buy\b",
    r"\bstrong_sell\b",
    r"\bmust_buy\b",
    r"\bmust_sell\b",
    r"\bпокупать\b",
    r"\bпродавать\b",
    r"\bthreshold\b",
]


def assert_no_forbidden_investment_vocabulary(payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False).lower()
    for pattern in FORBIDDEN_INVESTMENT_ACTION_PATTERNS:
        assert re.search(pattern, text) is None, (
            f"Forbidden vocabulary matched: {pattern}"
        )
