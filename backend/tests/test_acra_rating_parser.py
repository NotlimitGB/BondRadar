from datetime import date

import pytest

from app.services.credit_risk_evidence.acra.parser import parse_acra_issuer_page, parse_date
from app.services.credit_risk_evidence.acra.contracts import AcraError

URL = "https://www.acra-ratings.ru/ratings/issuers/123/?lang=en"
HTML = b'''<h1>Issuer <span>Name</span></h1>
<dl><dt>INN</dt><dd>7701234567</dd><dt>Country</dt><dd>Russia</dd></dl>
<h2>Credit rating</h2><h3>National rating scale</h3>
<table><tr><th>Rating</th><th>Outlook</th><th>Date</th></tr>
<tr><td>AA(RU)</td><td>Stable</td><td>9 Apr 2026</td></tr></table>
<h3>Rating history</h3><table><tr><th>Rating</th><th>Outlook</th><th>Date</th></tr>
<tr><td>AA(RU)</td><td>Stable</td><td>9 Apr 2026</td></tr>
<tr><td>Withdrawn</td><td>Under revision (developing)</td><td>30 Dec 2025</td></tr></table>
<h3>International rating scale</h3><table><tr><th>Rating</th><th>Date</th></tr>
<tr><td>BB+</td><td>9 Apr 2026</td></tr></table>
<h3>Issues ratings</h3><table><caption>National rating scale</caption>
<tr><th>Bond description</th><th>ISIN</th><th>Rating</th><th>Date</th></tr>
<tr><td>Bond</td><td>RU000A123456</td><td>AA(RU)</td><td>9 Apr 2026</td></tr></table>
<h2>ESG ratings</h2><table><tr><th>Rating</th><th>Date</th></tr>
<tr><td>ESG-A</td><td>9 Apr 2026</td></tr></table>'''


def test_current_history_multiscale_withdrawn_and_esg_exclusion():
    page = parse_acra_issuer_page(HTML, URL)
    assert page.inn_raw == "7701234567"
    assert len(page.issuer_rating_rows) == 3
    assert page.parser_duplicate_rows_removed == 1
    assert {r.scale_raw for r in page.issuer_rating_rows} == {"National rating scale", "International rating scale"}
    assert "Withdrawn" in {r.rating_value_raw for r in page.issuer_rating_rows}
    assert not any(r.rating_value_raw == "ESG-A" for r in page.issuer_rating_rows)
    assert page.issue_rating_rows[0].isin == "RU000A123456"
    assert page.diagnostics == ()


@pytest.mark.parametrize("outlook", ["Stable", "Positive", "Negative", "Developing", "Under revision (developing)"])
def test_outlook_preserved(outlook):
    page = parse_acra_issuer_page(HTML.replace(b"Stable", outlook.encode()), URL)
    assert outlook in {r.outlook_raw for r in page.issuer_rating_rows}


def test_missing_inn_keeps_explicit_issues():
    page = parse_acra_issuer_page(HTML.replace(b"<dt>INN</dt><dd>7701234567</dd>", b""), URL)
    assert page.inn_raw is None and len(page.issue_rating_rows) == 1


def test_invalid_date_and_unproven_scale_are_not_guessed():
    with pytest.raises(AcraError):
        parse_date("2026-04-09")
    broken = HTML.replace(b"9 Apr 2026", b"99 Apr 2026")
    assert "INVALID_VISIBLE_DATE" in parse_acra_issuer_page(broken, URL).diagnostics
    assert parse_date("30 Dec 2025") == date(2025, 12, 30)


def test_whitespace_nested_tags_and_missing_scale():
    page = parse_acra_issuer_page(HTML.replace(b"AA(RU)", b" <b>AA(RU)</b> \xc2\xa0"), URL)
    assert "AA(RU)" in {r.rating_value_raw for r in page.issuer_rating_rows}
    assert page.full_name_raw == "Issuer Name"
