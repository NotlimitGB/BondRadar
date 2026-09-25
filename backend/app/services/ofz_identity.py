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


def ofz_pd_family_marker(
    *,
    isin: str | None,
    secid: str | None,
    name: str | None,
    shortname: str | None = None,
) -> str | None:
    """Return an explicit OFZ-PD family token after canonical identity passes.

    Descriptive text classifies the subtype only; it never establishes OFZ
    identity. The token must have non-alphanumeric boundaries in at least
    one source-native name field.
    """

    if not is_ofz_instrument(isin=isin, secid=secid):
        return None

    markers = ("ОФЗ-ПД", "OFZ-PD")
    for description in (name, shortname):
        if not isinstance(description, str):
            continue
        normalized = description.strip().upper()
        for marker in markers:
            offset = normalized.find(marker)
            while offset >= 0:
                end = offset + len(marker)
                before_is_boundary = offset == 0 or not normalized[offset - 1].isalnum()
                after_is_boundary = end == len(normalized) or not normalized[end].isalnum()
                if before_is_boundary and after_is_boundary:
                    return marker
                offset = normalized.find(marker, offset + 1)
    return None


def is_ofz_pd_instrument(
    *,
    isin: str | None,
    secid: str | None,
    name: str | None,
    shortname: str | None = None,
) -> bool:
    """Return whether canonical OFZ identity has an explicit OFZ-PD marker."""

    return ofz_pd_family_marker(
        isin=isin,
        secid=secid,
        name=name,
        shortname=shortname,
    ) is not None
