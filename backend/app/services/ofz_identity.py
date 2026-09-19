"""Canonical, side-effect-free OFZ instrument identity semantics."""

from __future__ import annotations


def _normalized_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    return normalized or None


def is_ofz_instrument(*, isin: str | None, secid: str | None) -> bool:
    """Return whether strong identifiers establish Russian OFZ identity.

    A nonblank ISIN is authoritative. SECID is considered only when ISIN is
    absent or blank. Descriptive names are intentionally outside this
    contract and cannot establish OFZ identity.
    """

    normalized_isin = _normalized_identifier(isin)
    if normalized_isin is not None:
        return normalized_isin.startswith("SU")
    normalized_secid = _normalized_identifier(secid)
    return normalized_secid is not None and normalized_secid.startswith("SU")
