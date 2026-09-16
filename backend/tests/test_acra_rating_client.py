from datetime import datetime, timezone

import httpx
import pytest

from app.services.credit_risk_evidence.acra.client import AcraClient, parse_listing
from app.services.credit_risk_evidence.acra.contracts import AcraError, CATALOG_URL


LIST = b'<a href="/ratings/issuers/123/">Issuer</a><a href="https://foreign.invalid/ratings/issuers/5/">foreign</a>'


def make_client(handler):
    tick = [0.0]
    def sleep(n):
        tick[0] += n
    return AcraClient(client=httpx.Client(transport=httpx.MockTransport(handler)),
        monotonic=lambda: tick[0], sleep=sleep,
        clock=lambda: datetime(2026, 9, 16, tzinfo=timezone.utc))


def test_discovery_exact_links_robots_and_pacing():
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=LIST, headers={"content-type": "text/html"})
    client = make_client(handler)
    assert client.discover() == ("https://www.acra-ratings.ru/ratings/issuers/123/?lang=en",)
    assert client.http_requests == 2 and client.list_pages == 1
    assert calls[0].endswith("robots.txt")


def test_robots_disallow_and_tls_fail_closed():
    client = make_client(lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /ratings/issuers/"))
    with pytest.raises(AcraError, match="SOURCE_ACCESS_BLOCKED"):
        client.discover()
    assert client.http_requests == 1
    def broken(request):
        raise httpx.ConnectError("secret TLS details", request=request)
    with pytest.raises(AcraError, match="SOURCE_TRANSPORT_FAILURE"):
        make_client(broken).discover()


def test_pagination_loop_and_foreign_next():
    with pytest.raises(AcraError):
        parse_listing(LIST + b'<a rel="next" href="https://foreign.invalid/">Next</a>', CATALOG_URL)
    client = make_client(lambda r: httpx.Response(404) if r.url.path == "/robots.txt" else httpx.Response(
        200, content=LIST + b'<a rel="next" href="?lang=en">Next</a>', headers={"content-type":"text/html"}))
    with pytest.raises(AcraError, match="PAGINATION_LOOP"):
        client.discover()


def test_retries_retry_after_and_oversize(monkeypatch):
    attempts = []
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        attempts.append(1)
        return httpx.Response(429, headers={"retry-after":"1000"}) if len(attempts) < 3 else httpx.Response(200, content=LIST, headers={"content-type":"text/html"})
    client = make_client(handler)
    assert client.discover() and len(attempts) == 3
    assert client._retry_after("1000", 0) == 30
    monkeypatch.setattr("app.services.credit_risk_evidence.acra.client.MAX_RESPONSE_BYTES", 10)
    client = make_client(lambda r: httpx.Response(404) if r.url.path == "/robots.txt" else httpx.Response(200, content=LIST, headers={"content-type":"text/html"}))
    with pytest.raises(AcraError, match="OVERSIZED_RESPONSE"):
        client.discover()


def test_foreign_redirect_and_page_cap():
    client = make_client(lambda r: httpx.Response(404) if r.url.path == "/robots.txt" else httpx.Response(302, headers={"location":"http://127.0.0.1/"}))
    with pytest.raises(AcraError, match="UNSAFE_SOURCE_URL"):
        client.discover()
    client = make_client(lambda r: httpx.Response(404) if r.url.path == "/robots.txt" else httpx.Response(200, content=LIST + b'<a rel="next" href="?page=2">Next</a>', headers={"content-type":"text/html"}))
    with pytest.raises(AcraError, match="LIST_PAGE_CAP_REACHED"):
        client.discover(max_list_pages=1)


@pytest.mark.parametrize("marker", [b"CAPTCHA", b"cf-chl-challenge"])
def test_unavailable_robots_does_not_allow_bypassing_catalog_access_marker(marker):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=LIST + marker,
                              headers={"content-type": "text/html"})
    client = make_client(handler)
    with pytest.raises(AcraError, match="SOURCE_ACCESS_BLOCKED"):
        client.discover()
    assert client.robots.can_fetch("BondRadar-credit-research/1.0", CATALOG_URL)
    assert calls == ["https://www.acra-ratings.ru/robots.txt", CATALOG_URL]
    assert client.http_requests == 2
    assert client.list_pages == client.issuer_pages == 0
