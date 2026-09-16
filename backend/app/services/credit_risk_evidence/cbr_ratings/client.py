from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
import ssl
import time
from urllib.parse import urljoin, urlencode
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from app.services.credit_risk_evidence.contracts import canonical_inn, canonical_isin
from .contracts import (
    BASE, SEARCH_FIELDS, MAX_REQUESTS, MAX_RESPONSE_BYTES, MAX_SEARCH_PAGES,
    MAX_OBJECTS, MAX_BUNDLE_BYTES, SourceResponse, RepositoryError, safe_url, action_url, eligible_issuer_inns,
)
from .parser import envelope, parse_search, derive

USER_AGENT = "BondRadar-credit-research/1.0"
TRANSIENT = {429, 500, 502, 503, 504}


def search_fields(inn):
    result = dict.fromkeys(SEARCH_FIELDS, "")
    result.update(formSearh="advanced", inn=canonical_inn(inn), disclaimer="1")
    return result


class CbrRatingsClient:
    def __init__(self, *, client=None, clock=None, monotonic=time.monotonic, sleep=time.sleep):
        self.client = client or httpx.Client(verify=ssl.create_default_context(), trust_env=False,
            follow_redirects=False, timeout=httpx.Timeout(30, connect=5, write=10, pool=5))
        self.owned = client is None
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.monotonic, self.sleep = monotonic, sleep
        self.last_request = None
        self.requests = 0
        self.total_bytes = 0
        self.sessid = None
        self.robots = None
        self.stopped = False
        self.referer = BASE + "/?formSearh=advanced&disclaimer=1"

    def close(self):
        self.sessid = None
        self.client.cookies.clear()
        if self.owned:
            self.client.close()

    def _delay(self, value, attempt):
        delay = 2 ** (attempt + 1)
        if value:
            try:
                delay = max(delay, float(value))
            except ValueError:
                try:
                    delay = max(delay, (parsedate_to_datetime(value) - self.clock()).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    pass
        return min(30, max(0, delay))

    def _request(self, method, url, *, body=None, robots=False):
        safe_url(url)
        if self.stopped:
            raise RepositoryError("SOURCE_RUN_STOPPED")
        if not robots and (self.robots is None or not self.robots.can_fetch(USER_AGENT, url)):
            raise RepositoryError("ROBOTS_DISALLOW")
        current, redirects = url, 0
        for attempt in range(3):
            try:
                while True:
                    if self.requests >= MAX_REQUESTS:
                        raise RepositoryError("REQUEST_LIMIT")
                    if self.last_request is not None:
                        self.sleep(max(0, 1 - (self.monotonic() - self.last_request)))
                    self.last_request = self.monotonic(); self.requests += 1
                    headers = {"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"}
                    if method == "POST":
                        headers.update({"Content-Type": "application/x-www-form-urlencoded",
                            "X-Requested-With": "XMLHttpRequest", "Referer": self.referer})
                    with self.client.stream(method, current, content=body, headers=headers) as r:
                        if sum(len(k) + len(v) for k, v in r.headers.items()) > 65536:
                            raise RepositoryError("OVERSIZED_HEADERS")
                        if r.status_code in (301, 302, 303, 307, 308):
                            if redirects >= 3 or not r.headers.get("location"):
                                raise RepositoryError("REDIRECT_LIMIT")
                            target = safe_url(urljoin(current, r.headers["location"]))
                            if method == "POST" and (r.status_code not in (307, 308) or target != url):
                                raise RepositoryError("UNSAFE_ACTION_REDIRECT")
                            if not robots and not self.robots.can_fetch(USER_AGENT, target):
                                raise RepositoryError("ROBOTS_DISALLOW")
                            current = target; redirects += 1
                            continue
                        if robots and r.status_code == 404:
                            return None
                        if r.status_code in TRANSIENT:
                            if attempt == 2:
                                raise RepositoryError("TRANSIENT_SOURCE_FAILURE")
                            delay = self._delay(r.headers.get("retry-after"), attempt)
                            break
                        if r.status_code != 200:
                            raise RepositoryError("SOURCE_ACCESS_BLOCKED")
                        length = r.headers.get("content-length")
                        if length is not None:
                            try:
                                if not 0 <= int(length) <= MAX_RESPONSE_BYTES:
                                    raise RepositoryError("OVERSIZED_RESPONSE")
                            except ValueError as e:
                                if isinstance(e, RepositoryError):
                                    raise
                                raise RepositoryError("INVALID_CONTENT_LENGTH") from None
                        content_type = r.headers.get("content-type", "")
                        allowed = ("application/json",) if method == "POST" else ("text/html", "application/xhtml+xml")
                        if not robots and content_type.split(";", 1)[0].lower() not in allowed:
                            raise RepositoryError("INVALID_CONTENT_TYPE")
                        payload = bytearray()
                        for chunk in r.iter_bytes():
                            if len(payload) + len(chunk) > MAX_RESPONSE_BYTES:
                                raise RepositoryError("OVERSIZED_RESPONSE")
                            payload.extend(chunk)
                        if not payload:
                            raise RepositoryError("EMPTY_RESPONSE")
                        return bytes(payload), content_type
                self.sleep(delay)
            except httpx.TimeoutException:
                if attempt == 2:
                    raise RepositoryError("SOURCE_TIMEOUT") from None
                self.sleep(2 ** (attempt + 1))
            except httpx.RequestError as e:
                if "connection reset" in str(e).casefold() and attempt < 2:
                    self.sleep(2 ** (attempt + 1))
                    continue
                raise RepositoryError("SOURCE_TRANSPORT_FAILURE") from None
        raise RepositoryError("TRANSIENT_SOURCE_FAILURE")

    def bootstrap(self):
        resource = self._request("GET", BASE + "/robots.txt", robots=True)
        rules = RobotFileParser(BASE + "/robots.txt")
        try:
            robots_text = "" if resource is None else resource[0].decode("utf-8", "strict")
            if any(marker in robots_text.casefold() for marker in ("<html", "cf-chl-", "captcha required", "access denied")):
                raise RepositoryError("SOURCE_ACCESS_BLOCKED")
            rules.parse(robots_text.splitlines())
        except UnicodeError:
            raise RepositoryError("INVALID_ROBOTS") from None
        self.robots = rules
        payload, _ = self._request("GET", self.referer)
        try:
            html = payload.decode("utf-8", "strict")
        except UnicodeError:
            raise RepositoryError("INVALID_BOOTSTRAP") from None
        soup = BeautifulSoup(html, "html.parser")
        if soup.select("[name='captchaCode'][required], .g-recaptcha, [data-sitekey]") or "cf-chl-" in html.lower():
            self.stopped = True
            raise RepositoryError("CAPTCHA_REQUIRED")
        if "access denied" in html.casefold():
            raise RepositoryError("SOURCE_ACCESS_BLOCKED")
        values = {node.get("value") for node in soup.select("input[name='sessid']")}
        values.update(re.findall(r'["\']bitrix_sessid["\']\s*:\s*["\']([0-9a-fA-F]+)["\']', html))
        values.update(re.findall(r'bitrix_sessid\s*=\s*function\s*\(\)\s*\{\s*return\s*["\']([0-9a-fA-F]+)["\']', html))
        if len(values) != 1 or not isinstance(next(iter(values)), str) or not re.fullmatch(r"[0-9a-fA-F]{16,64}", next(iter(values))):
            raise RepositoryError("UNSUPPORTED_SESSION_BOOTSTRAP")
        self.sessid = next(iter(values))

    def action(self, action, fields):
        if self.sessid is None:
            raise RepositoryError("SESSION_NOT_INITIALIZED")
        body = urlencode({"sessid": self.sessid, **{"fields[" + k + "]": str(v) for k, v in fields.items()}}).encode("ascii")
        payload, content_type = self._request("POST", action_url(action), body=body)
        try:
            if action == "searchRating":
                parse_search(payload, action=action)
            else:
                envelope(payload)
            if self.sessid.encode("ascii") in payload:
                raise RepositoryError("SECRET_BEARING_RESPONSE")
        except RepositoryError as e:
            if e.code == "CAPTCHA_REQUIRED":
                self.stopped = True
            raise
        self.total_bytes += len(payload)
        if self.total_bytes > MAX_BUNDLE_BYTES:
            raise RepositoryError("BUNDLE_BYTE_LIMIT")
        return SourceResponse(action, tuple(sorted((k, str(v)) for k, v in fields.items())), payload, self.clock(), content_type)

    def collect(self, universe):
        self.bootstrap()
        responses, retained = [], set()
        for inn in eligible_issuer_inns(universe["issuer_inns"]):
            self.referer = BASE + "/?" + urlencode({"formSearh": "advanced", "inn": inn, "disclaimer": "1"})
            response = self.action("searchRating", search_fields(inn)); responses.append(response)
            first = parse_search(response.content, action=response.action)
            if first.page_number not in ((1,) if first.item_count else (0, 1)):
                raise RepositoryError("INVALID_PAGINATION")
            if first.page_count > MAX_SEARCH_PAGES:
                raise RepositoryError("SEARCH_PAGE_LIMIT")
            pages = [first]
            for number in range(2, first.page_count + 1):
                response = self.action("searchRatingNavigation", {"pageSize": first.page_size, "pageNumber": number,
                    "sortingField": first.sorting_field, "sortingDirection": first.sorting_direction})
                page = parse_search(response.content, action=response.action)
                if page.page_number != number or (page.item_count, page.page_count, page.page_size,
                    page.sorting_field, page.sorting_direction) != (first.item_count, first.page_count,
                    first.page_size, first.sorting_field, first.sorting_direction):
                    raise RepositoryError("PAGINATION_CONTEXT_CONFLICT")
                responses.append(response); pages.append(page)
            for page in pages:
                for fields in page.rows:
                    row = dict(fields)
                    if row["inn"] and canonical_inn(row["inn"]) != inn:
                        raise RepositoryError("FOREIGN_ISSUER_INN")
                    if row["isin"]:
                        canonical_isin(row["isin"])
                    if not row["isin"] or row["isin"] in universe["bond_isins"]:
                        retained.add(row["objectId"])
            if len(retained) > MAX_OBJECTS:
                raise RepositoryError("OBJECT_LIMIT")
        for obj in sorted(retained, key=int):
            responses.append(self.action("searchObjectHistory", {"objectId": obj}))
        derive(tuple(responses), universe)  # Full pagination, identity and history completion gate.
        return tuple(responses)
