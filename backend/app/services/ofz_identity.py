"""Canonical, side-effect-free OFZ instrument identity semantics."""

from __future__ import annotations


def _normalized_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    return normalized or None


def is_ofz_instrument(*, isin: str | None, secid: str | None) -> bool:
    """Return whether strong identifiers establish Russian OFZ identity.

    A normalized SU-prefixed SECID or ISIN is sufficient. Descriptive names
    are intentionally outside this contract and cannot establish OFZ
    identity.
    """

    normalized_isin = _normalized_identifier(isin)
    normalized_secid = _normalized_identifier(secid)
    return (
        (normalized_secid is not None and normalized_secid.startswith("SU"))
        or (normalized_isin is not None and normalized_isin.startswith("SU"))
    )
