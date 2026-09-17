from datetime import datetime
import json
import math
import re

from app.services.credit_risk_evidence.contracts import (
    RatingEventInput, RatingTarget, PublicationPrecision, canonical_inn, canonical_isin,
)
from .contracts import AGENCIES, ITEM_FIELDS, SEARCH_FIELDS, MAX_OBJECTS, MAX_RESPONSE_BYTES, RepositoryError, SearchPage, Candidate, eligible_issuer_inns


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise RepositoryError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _inspect(value, depth=0):
    if depth > 20:
        raise RepositoryError("INVALID_SOURCE_JSON")
    if isinstance(value, dict):
        if len(value) > 64 or any(len(k) > 256 for k in value):
            raise RepositoryError("OVERSIZED_SOURCE_FIELD")
        for key, item in value.items():
            if key.casefold() in {"sessid", "bitrix_sessid", "sessionid", "cookies", "cookie", "set-cookie", "captchacode", "captchaid"}:
                raise RepositoryError("SECRET_BEARING_RESPONSE")
            if key.casefold() == "captcharesult" and item is False:
                raise RepositoryError("CAPTCHA_REQUIRED")
            if key.casefold() in {"captcharequired", "requirecaptcha"} and item is True:
                raise RepositoryError("CAPTCHA_REQUIRED")
            _inspect(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > MAX_OBJECTS:
            raise RepositoryError("SOURCE_ROW_LIMIT")
        for item in value:
            _inspect(item, depth + 1)
    elif isinstance(value, str) and len(value) > 8192:
        raise RepositoryError("OVERSIZED_SOURCE_FIELD")
    elif isinstance(value, float) and not math.isfinite(value):
        raise RepositoryError("INVALID_SOURCE_JSON")


def _decode_payload(content):
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_RESPONSE_BYTES:
        raise RepositoryError("INVALID_SOURCE_JSON")
    try:
        payload = json.loads(content.decode("utf-8", "strict"), object_pairs_hook=_pairs,
                             parse_constant=lambda _: (_ for _ in ()).throw(RepositoryError("INVALID_SOURCE_JSON")))
        _inspect(payload)
        return payload
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, RepositoryError):
            raise
        raise RepositoryError("INVALID_SOURCE_JSON") from None


def _success_data(payload):
    if not isinstance(payload, dict) or payload.get("status") != "success" or payload.get("errors") != [] or not isinstance(payload.get("data"), dict):
        raise RepositoryError("BITRIX_SOURCE_ERROR")
    return payload["data"]


def envelope(content):
    return _success_data(_decode_payload(content))


def _is_explicit_empty_search(payload):
    if not isinstance(payload, dict) or set(payload) != {"status", "data", "errors"}:
        return False
    errors = payload["errors"]
    if payload["status"] != "error" or payload["data"] is not None or not isinstance(errors, list) or len(errors) != 1:
        return False
    error = errors[0]
    return (isinstance(error, dict) and set(error) in ({"code", "message"}, {"code", "message", "customData"})
            and error.get("customData") is None
            and type(error["code"]) is int and error["code"] == 0 and error["message"] == "Array")


def text(value, *, optional=False, limit=512):
    if not isinstance(value, str) or len(value) > limit:
        raise RepositoryError("INVALID_SOURCE_FIELD")
    return value or None if optional else value


def source_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}(?: \d{2}:\d{2}(?::\d{2})?)?", value):
        raise RepositoryError("INVALID_RELEASE_DATE")
    try:
        fmt = "%d.%m.%Y" + (" %H:%M:%S" if len(value) == 19 else " %H:%M" if len(value) == 16 else "")
        return datetime.strptime(value, fmt).date()
    except ValueError:
        raise RepositoryError("INVALID_RELEASE_DATE") from None


def agency(value):
    try:
        return AGENCIES[text(value).strip()]
    except KeyError:
        raise RepositoryError("UNKNOWN_RATING_AGENCY") from None


def _parse_search_result(content, *, action):
    if action not in ("searchRating", "searchRatingNavigation"):
        raise RepositoryError("UNSUPPORTED_ACTION")
    payload = _decode_payload(content)
    if action == "searchRating" and _is_explicit_empty_search(payload):
        # Only the derived page is synthetic; the source response bytes stay intact.
        return SearchPage(0, 0, 0, 25, "objectName", "ascending", ()), True
    data = _success_data(payload)
    for key, minimum in (("itemCount", 0), ("pageCount", 0), ("pageNumber", 0), ("pageSize", 1)):
        if type(data.get(key)) is not int or data[key] < minimum:
            raise RepositoryError("INVALID_PAGINATION")
    if data.get("sortingField") != "objectName" or data.get("sortingDirection") != "ascending":
        raise RepositoryError("UNSUPPORTED_SORTING")
    if data.get("itemFields") != list(ITEM_FIELDS) or not isinstance(data.get("itemList"), list):
        raise RepositoryError("INVALID_SEARCH_SCHEMA")
    count, pages, number, size = (data[k] for k in ("itemCount", "pageCount", "pageNumber", "pageSize"))
    if size != 25 or count > MAX_OBJECTS or pages != (count + size - 1) // size:
        raise RepositoryError("INVALID_PAGINATION")
    if (count and not 1 <= number <= pages) or (not count and number not in (0, 1)):
        raise RepositoryError("INVALID_PAGINATION")
    expected = min(size, max(0, count - (number - 1) * size)) if count else 0
    if len(data["itemList"]) != expected:
        raise RepositoryError("INCOMPLETE_SEARCH_PAGE")
    rows = []
    for row in data["itemList"]:
        if not isinstance(row, dict) or not set(ITEM_FIELDS).issubset(row):
            raise RepositoryError("INVALID_SEARCH_ROW")
        fields = tuple((k, text(row[k], limit=2048 if k == "releaseUrl" else 512)) for k in ITEM_FIELDS)
        obj = dict(fields)["objectId"]
        if not re.fullmatch(r"[1-9][0-9]{0,19}", obj):
            raise RepositoryError("INVALID_OBJECT_ID")
        agency(row["kraName"])
        rows.append(fields)
    return SearchPage(count, pages, number, size, data["sortingField"], data["sortingDirection"], tuple(rows)), False


def parse_search(content, *, action="searchRating"):
    return _parse_search_result(content, action=action)[0]


def _event(row, identity, object_id, *, history=False):
    target, inn, isin, name = identity
    released = source_date(row["releaseDate"])
    return RatingEventInput(
        agency=agency(row["kraName"]), target=RatingTarget(target),
        source_object_id="CBR_RATINGS_OBJECT:" + object_id,
        source_issuer_inn=inn, source_bond_isin=isin, source_name_raw=name,
        event_date=released, publication_precision=PublicationPrecision.DATE,
        publication_date=released, publication_at=None, rating_scale_raw=None,
        rating_value_raw=text(row["ratingValue"], optional=True, limit=128),
        rating_outlook_raw=None if history else text(row["prediction"], optional=True, limit=256),
        rating_action_raw=None if history else text(row["ratingAction"], optional=True, limit=256),
    )


def logical_key(value):
    return (value.source_object_id, value.agency.value, value.target.value,
            value.source_issuer_inn, value.source_bond_isin, value.event_date, value.rating_value_raw)


def derive(responses, universe):
    objects, current, history, completed = {}, [], [], set()
    eligible = set(eligible_issuer_inns(universe["issuer_inns"]))
    seen_inns, active, next_page, config, seen_rows = set(), None, None, None, 0
    counts = {k: 0 for k in ("issuer_queries_attempted", "issuer_queries_succeeded", "issuer_queries_no_results", "search_pages_fetched",
        "search_rows_seen", "unique_object_ids_seen", "issuer_objects_in_universe", "bond_objects_in_universe",
        "bond_objects_outside_universe", "object_histories_fetched", "history_rows_seen", "current_history_duplicates")}
    counts.update(issuer_query_eligible_count=len(eligible),
                  issuer_query_ineligible_count=len(universe["issuer_inns"]) - len(eligible))
    for index, response in enumerate(responses):
        fields = dict(response.fields)
        if len(fields) != len(response.fields) or response.fields != tuple(sorted(response.fields)):
            raise RepositoryError("INVALID_REQUEST_METADATA")
        if response.action == "searchRating":
            if set(fields) != set(SEARCH_FIELDS) or fields.get("formSearh") != "advanced" or fields.get("disclaimer") != "1" or any(fields[k] != "" for k in SEARCH_FIELDS if k not in {"inn", "formSearh", "disclaimer"}):
                raise RepositoryError("INVALID_REQUEST_METADATA")
        elif response.action == "searchRatingNavigation":
            if set(fields) != {"pageSize", "pageNumber", "sortingField", "sortingDirection"} or fields["pageSize"] != "25" or fields["sortingField"] != "objectName" or fields["sortingDirection"] != "ascending" or not re.fullmatch(r"[1-9][0-9]{0,2}", fields["pageNumber"]):
                raise RepositoryError("INVALID_REQUEST_METADATA")
        elif response.action == "searchObjectHistory":
            if set(fields) != {"objectId"} or not re.fullmatch(r"[1-9][0-9]{0,19}", fields["objectId"]):
                raise RepositoryError("INVALID_REQUEST_METADATA")
        if response.action in ("searchRating", "searchRatingNavigation"):
            page, explicit_empty = _parse_search_result(response.content, action=response.action)
            if response.action == "searchRating":
                inn = canonical_inn(fields.get("inn"))
                if inn not in eligible or inn in seen_inns or next_page is not None:
                    raise RepositoryError("SEARCH_CONTEXT_CONFLICT")
                seen_inns.add(inn); active = inn
                config = (page.item_count, page.page_count, page.page_size, page.sorting_field, page.sorting_direction)
                if page.page_number not in ((1,) if page.item_count else (0, 1)):
                    raise RepositoryError("INVALID_PAGINATION")
                counts["issuer_queries_attempted"] += 1
                counts["issuer_queries_no_results"] += int(explicit_empty)
            else:
                if next_page is None or int(fields["pageNumber"]) != next_page or page.page_number != next_page or (page.item_count, page.page_count, page.page_size, page.sorting_field, page.sorting_direction) != config:
                    raise RepositoryError("PAGINATION_CONTEXT_CONFLICT")
            next_page = page.page_number + 1 if page.page_number < page.page_count else None
            counts["issuer_queries_succeeded"] += int(next_page is None)
            counts["search_pages_fetched"] += 1
            for pairs in page.rows:
                row = dict(pairs); seen_rows += 1
                inn, isin = row["inn"], row["isin"]
                if isin:
                    canonical_isin(isin)
                    if inn:
                        canonical_inn(inn)
                    identity = ("BOND", None, isin, row["objectName"] or None)
                elif inn:
                    if canonical_inn(inn) != active:
                        raise RepositoryError("FOREIGN_ISSUER_INN")
                    identity = ("LEGAL_ISSUER", canonical_inn(inn), None, row["objectName"] or None)
                else:
                    raise RepositoryError("UNRESOLVED_SOURCE_IDENTITY")
                obj = row["objectId"]
                # Retain the source INN as well as canonical target; it is collision lineage for bonds.
                binding = (identity[:3], inn, isin)
                if obj in objects and objects[obj][1] != binding:
                    raise RepositoryError("OBJECT_IDENTITY_COLLISION")
                retained = not isin or isin in universe["bond_isins"]
                objects[obj] = (identity, binding, retained)
                if len(objects) > MAX_OBJECTS:
                    raise RepositoryError("OBJECT_LIMIT")
                if retained:
                    current.append(Candidate(_event(row, identity, obj), index))
        elif response.action == "searchObjectHistory":
            obj = fields.get("objectId")
            if next_page is not None or obj not in objects or not objects[obj][2] or obj in completed:
                raise RepositoryError("INVALID_HISTORY_LINEAGE")
            data = envelope(response.content)
            if not isinstance(data.get("title"), str) or not isinstance(data.get("table"), list):
                raise RepositoryError("INVALID_HISTORY_SCHEMA")
            for row in data["table"]:
                if not isinstance(row, dict) or not {"ratingValue", "kraName", "releaseDate"}.issubset(row):
                    raise RepositoryError("INVALID_HISTORY_ROW")
                history.append(Candidate(_event(row, objects[obj][0], obj, history=True), index))
            completed.add(obj)
            counts["object_histories_fetched"] += 1
            counts["history_rows_seen"] += len(data["table"])
        else:
            raise RepositoryError("UNSUPPORTED_ACTION")
    retained = {obj for obj, item in objects.items() if item[2]}
    if next_page is not None or seen_inns != eligible or completed != retained:
        raise RepositoryError("INCOMPLETE_DISCOVERY")
    keys = {logical_key(c.value) for c in current}
    counts["current_history_duplicates"] = sum(logical_key(c.value) in keys for c in history)
    candidates = current + [c for c in history if logical_key(c.value) not in keys]
    unique = {}
    for c in candidates:
        # Full source semantics; source name and payload context are diagnostics only.
        v = c.value
        key = logical_key(v) + (v.rating_outlook_raw, v.rating_watch_raw, v.rating_action_raw)
        unique.setdefault(key, c)
    counts.update(search_rows_seen=seen_rows, unique_object_ids_seen=len(objects),
                  issuer_objects_in_universe=sum(x[2] and x[0][0] == "LEGAL_ISSUER" for x in objects.values()),
                  bond_objects_in_universe=sum(x[2] and x[0][0] == "BOND" for x in objects.values()),
                  bond_objects_outside_universe=sum(not x[2] for x in objects.values()))
    return tuple(unique[k] for k in sorted(unique, key=repr)), counts, retained
