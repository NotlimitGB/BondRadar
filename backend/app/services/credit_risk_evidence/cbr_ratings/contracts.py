from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit
import re

from app.services.credit_risk_evidence.contracts import RatingAgency, RatingEventInput

HOST = "ratings.cbr.ru"
BASE = f"https://{HOST}"
REVISION = "202609160001"
CONTRACT = "cbr-rating-repository-ingestion-v1"
SCHEMA = "bondradar.cbr_rating_repository_ingestion_runner.v1"
BUNDLE_SCHEMA = "bondradar.cbr_rating_repository_ingestion_bundle.v1"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_BUNDLE_BYTES = 128 * 1024 * 1024
MAX_REQUESTS = 10000
MAX_SEARCH_PAGES = 100
MAX_OBJECTS = 10000
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
ACTIONS = ("searchRating", "searchRatingNavigation", "searchObjectHistory")
AGENCIES = {'АКРА (АО)': RatingAgency.ACRA, 'АО "Эксперт РА"': RatingAgency.EXPERT_RA,
            'ООО "НРА"': RatingAgency.NRA, 'ООО "НКР"': RatingAgency.NKR}
SEARCH_FIELDS = ("formSearh", "captchaCode", "dateFrom", "dateTo", "ratingName", "inn",
                 "isin", "koNumber", "kraName", "ratingAction", "ratingGroup", "ratingScale",
                 "ratingStatus", "typeGroup", "country", "disclaimer")
ITEM_FIELDS = ("objectId", "objectName", "subjectName", "country", "objectType", "inn", "isin",
               "koNumber", "ratingValue", "prediction", "kraName", "releaseDate", "ratingAction", "releaseUrl")
SHA = re.compile(r"[0-9a-f]{64}")


def is_russian_legal_entity_inn(value: str) -> bool:
    """Source-query eligibility only; not the canonical evidence identity rule."""
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{10}", value) is None:
        return False
    weights = (2, 4, 10, 3, 5, 9, 4, 6, 8)
    control = sum(int(digit) * weight for digit, weight in zip(value[:9], weights)) % 11 % 10
    return control == int(value[9])


def eligible_issuer_inns(issuer_inns):
    """Recompute the ordered query scope from the full frozen issuer universe."""
    return tuple(sorted({value for value in issuer_inns if is_russian_legal_entity_inn(value)}))


class RepositoryError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def safe_url(url):
    try:
        p = urlsplit(url)
        valid = (p.scheme == "https" and p.hostname == HOST and p.port in (None, 443)
                 and p.username is None and p.password is None and not p.fragment
                 and not any(ord(c) < 33 for c in url) and "\\" not in url)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise RepositoryError("UNSAFE_SOURCE_URL")
    return url


def action_url(action):
    if action not in ACTIONS:
        raise RepositoryError("UNSUPPORTED_ACTION")
    return BASE + "/bitrix/services/main/ajax.php?mode=ajax&c=prr.form&action=" + action


@dataclass(frozen=True, slots=True)
class SourceResponse:
    action: str
    fields: tuple[tuple[str, str], ...]
    content: bytes
    retrieved_at: datetime
    content_type: str = "application/json"

    @property
    def source_url(self):
        return action_url(self.action)


@dataclass(frozen=True, slots=True)
class Candidate:
    value: RatingEventInput
    response_index: int


@dataclass(frozen=True, slots=True)
class SearchPage:
    item_count: int
    page_count: int
    page_number: int
    page_size: int
    sorting_field: str
    sorting_direction: str
    rows: tuple[tuple[tuple[str, str], ...], ...]
