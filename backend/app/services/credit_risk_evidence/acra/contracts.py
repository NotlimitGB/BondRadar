from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.services.credit_risk_evidence.contracts import canonical_json_sha256


SCHEMA = "bondradar.acra_rating_ingestion_runner.v1"
BUNDLE_SCHEMA = "bondradar.acra_rating_ingestion_bundle.v1"
CONTRACT = "acra-rating-ingestion-v1"
REVISION = "202609150003"
HOST = "www.acra-ratings.ru"
CATALOG_URL = f"https://{HOST}/ratings/issuers/?lang=en"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_LIST_PAGES = 100
MAX_BUNDLE_PAGES = 10000
MAX_BUNDLE_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024


class AcraError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def safe_url(value: str) -> str:
    try:
        p = urlsplit(value)
        valid = (
            p.scheme == "https" and p.hostname == HOST
            and p.username is None and p.password is None
            and p.port in (None, 443) and not p.fragment
            and "\\" not in value and not any(ord(c) < 33 for c in value)
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise AcraError("UNSAFE_SOURCE_URL")
    return value


def issuer_url(value: str) -> tuple[str, str]:
    p = urlsplit(safe_url(value))
    match = re.fullmatch(r"/ratings/issuers/([1-9][0-9]*)/?", p.path)
    if match is None:
        raise AcraError("INVALID_ISSUER_ROUTE")
    return f"https://{HOST}/ratings/issuers/{match[1]}/?lang=en", match[1]


def listing_url(value: str) -> str:
    p = urlsplit(safe_url(value))
    if p.path.rstrip("/") != "/ratings/issuers":
        raise AcraError("INVALID_LISTING_ROUTE")
    pairs = parse_qsl(p.query, keep_blank_values=True)
    if len(pairs) > 8 or len({k for k, _ in pairs}) != len(pairs):
        raise AcraError("INVALID_PAGINATION")
    pairs = [(k, v) for k, v in pairs if k != "lang"] + [("lang", "en")]
    return urlunsplit(("https", HOST, "/ratings/issuers/", urlencode(sorted(pairs)), ""))


@dataclass(frozen=True, slots=True)
class AcraRatingRow:
    scale_raw: str | None
    rating_value_raw: str | None
    outlook_raw: str | None
    watch_raw: str | None
    action_raw: str | None
    visible_date: date
    source_release_url: str | None
    source_section: str
    isin: str | None = None
    source_name_raw: str | None = None


@dataclass(frozen=True, slots=True)
class AcraIssuerPage:
    acra_issuer_id: str
    source_url: str
    full_name_raw: str | None
    russian_name_raw: str | None
    country_raw: str | None
    sector_raw: str | None
    activity_raw: str | None
    inn_raw: str | None
    ogrn_raw: str | None
    issuer_rating_rows: tuple[AcraRatingRow, ...]
    issue_rating_rows: tuple[AcraRatingRow, ...]
    diagnostics: tuple[str, ...]
    parser_duplicate_rows_removed: int


@dataclass(frozen=True, slots=True)
class FetchedPage:
    canonical_url: str
    content_bytes: bytes
    content_type: str
    retrieved_at: datetime


def semantic_row_key(row: AcraRatingRow) -> tuple:
    return (
        row.scale_raw, row.rating_value_raw, row.outlook_raw, row.watch_raw,
        row.action_raw, row.visible_date, row.isin, row.source_name_raw,
    )


def universe_hash(inns: tuple[str, ...], isins: tuple[str, ...]) -> str:
    return canonical_json_sha256({"issuer_inns": sorted(set(inns)), "bond_isins": sorted(set(isins))})
