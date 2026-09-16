from __future__ import annotations

from datetime import date
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.services.credit_risk_evidence.acra.contracts import (
    AcraError, AcraIssuerPage, AcraRatingRow, issuer_url, safe_url, semantic_row_key,
)
from app.services.credit_risk_evidence.contracts import canonical_inn, canonical_isin


MONTHS = {name: index for index, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
)}


def text(node) -> str:
    return " ".join(node.get_text(" ", strip=True).split())


def parse_date(value: str) -> date:
    match = re.fullmatch(r"([0-9]{1,2}) ([A-Z][a-z]{2}) ([0-9]{4})", value)
    try:
        if match is None:
            raise ValueError()
        return date(int(match[3]), MONTHS[match[2]], int(match[1]))
    except (ValueError, KeyError):
        raise AcraError("INVALID_VISIBLE_DATE") from None


def parse_acra_issuer_page(content_bytes: bytes, source_url: str) -> AcraIssuerPage:
    canonical, source_id = issuer_url(source_url)
    try:
        soup = BeautifulSoup(content_bytes.decode("utf-8", "strict"), "html.parser")
    except (UnicodeError, AttributeError):
        raise AcraError("INVALID_HTML_ENCODING") from None
    metadata = {}
    labels = {"full name": "full", "russian name": "russian", "country": "country",
              "sector": "sector", "activity": "activity", "inn": "inn", "tin": "inn", "ogrn": "ogrn"}
    diagnostics = []
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) == 2 and text(cells[0]).casefold().rstrip(":") in labels:
            key = labels[text(cells[0]).casefold().rstrip(":")]
            value = text(cells[1]) or None
            if key in metadata and metadata[key] != value:
                diagnostics.append("AMBIGUOUS_METADATA")
            metadata[key] = value
    for dt in soup.find_all("dt"):
        label = text(dt).casefold().rstrip(":")
        dd = dt.find_next_sibling("dd")
        if label in labels and dd is not None:
            key, value = labels[label], text(dd) or None
            if key in metadata and metadata[key] != value:
                diagnostics.append("AMBIGUOUS_METADATA")
            metadata[key] = value
    h1 = soup.find("h1")
    metadata.setdefault("full", text(h1) if h1 else None)
    if metadata.get("inn"):
        try:
            canonical_inn(metadata["inn"])
        except ValueError:
            diagnostics.append("INVALID_SOURCE_INN")

    current_section, scale, credit_active = "CURRENT", None, False
    saw_credit = False
    issuer_rows, issue_rows = [], []
    for node in soup.find_all(["h2", "h3", "h4", "table"]):
        if node.name != "table":
            heading = text(node)
            label = heading.casefold()
            if label == "credit rating":
                credit_active, saw_credit, current_section, scale = True, True, "CURRENT", None
            elif label in ("esg rating", "esg ratings", "stock rating", "sustainability assessments", "analytical research"):
                credit_active = False
            elif credit_active and label == "rating history":
                current_section = "HISTORY"
            elif credit_active and label in ("issues ratings", "issue ratings"):
                current_section = "ISSUES"
            elif credit_active and label in ("national rating scale", "international rating scale"):
                scale = heading
            elif node.name == "h2":
                credit_active = False
            continue
        if not credit_active:
            continue
        table_scale = scale
        caption = node.find("caption")
        if caption and text(caption).casefold() in ("national rating scale", "international rating scale"):
            table_scale = text(caption)
        rows = node.find_all("tr")
        if not rows:
            continue
        header_cells = rows[0].find_all(["th", "td"], recursive=False)
        headers = [text(cell).casefold() for cell in header_cells]
        if len(headers) != len(set(headers)):
            diagnostics.append("AMBIGUOUS_RATING_HEADERS")
            continue
        aliases = {"rating": "rating", "credit rating": "rating", "date": "date",
                   "rating date": "date", "outlook": "outlook", "watch": "watch",
                   "action": "action", "rating scale": "scale", "isin": "isin",
                   "bond description": "name", "name": "name"}
        bound = [aliases.get(label) for label in headers]
        if "rating" not in bound or "date" not in bound:
            diagnostics.append("UNSUPPORTED_CREDIT_TABLE")
            continue
        for tr in rows[1:]:
            cells = tr.find_all(["th", "td"], recursive=False)
            if len(cells) != len(headers):
                diagnostics.append("INVALID_RATING_ROW")
                continue
            values = {key: text(cell) or None for key, cell in zip(bound, cells) if key}
            try:
                visible_date = parse_date(values.get("date") or "")
                isin = values.get("isin")
                if isin is not None:
                    canonical_isin(isin)
                release_links = []
                for anchor in tr.find_all("a", href=True):
                    link = urljoin(canonical, anchor["href"])
                    try:
                        safe_url(link)
                    except AcraError:
                        continue
                    if "/press-releases/" in link:
                        release_links.append(link)
                if len(set(release_links)) > 1:
                    raise AcraError("AMBIGUOUS_RELEASE_LINK")
                row_scale = values.get("scale") or table_scale
                if current_section != "ISSUES" and row_scale is None:
                    raise AcraError("ISSUER_SCALE_UNPROVEN")
                if not any(values.get(k) for k in ("rating", "outlook", "watch", "action")):
                    raise AcraError("EMPTY_RATING_SEMANTICS")
                item = AcraRatingRow(
                    row_scale, values.get("rating"), values.get("outlook"), values.get("watch"),
                    values.get("action"), visible_date,
                    release_links[0] if release_links else None, current_section,
                    isin, values.get("name"),
                )
                (issue_rows if current_section == "ISSUES" else issuer_rows).append(item)
            except (AcraError, ValueError) as exc:
                diagnostics.append(getattr(exc, "code", "INVALID_SOURCE_ISIN"))
    if not saw_credit:
        diagnostics.append("CREDIT_SECTION_NOT_FOUND")
    duplicates = 0
    def dedupe(rows):
        nonlocal duplicates
        result, seen = [], set()
        for row in rows:
            key = semantic_row_key(row)
            if key in seen:
                duplicates += 1
            else:
                seen.add(key)
                result.append(row)
        return tuple(sorted(result, key=lambda r: (r.visible_date, repr(semantic_row_key(r)))))
    issuers, issues = dedupe(issuer_rows), dedupe(issue_rows)
    return AcraIssuerPage(
        source_id, canonical, metadata.get("full"), metadata.get("russian"),
        metadata.get("country"), metadata.get("sector"), metadata.get("activity"),
        metadata.get("inn"), metadata.get("ogrn"), issuers, issues,
        tuple(sorted(set(diagnostics))), duplicates,
    )
