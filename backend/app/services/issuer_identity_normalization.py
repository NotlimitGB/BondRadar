from __future__ import annotations

import re


LEGAL_FORMS = {
    "ооо",
    "оао",
    "пао",
    "ао",
    "зао",
    "ooo",
    "oao",
    "pao",
    "ao",
    "zao",
    "ltd",
    "llc",
    "plc",
}
BOND_SUFFIX_TOKENS = {
    "бо",
    "обл",
    "облигации",
    "облигация",
    "выпуск",
    "вып",
    "series",
    "серия",
}


def normalize_issuer_name(value: str | None) -> str:
    text = _normalize_text(value)
    tokens = [
        token
        for token in text.split()
        if token not in LEGAL_FORMS and token not in BOND_SUFFIX_TOKENS
    ]
    return " ".join(tokens)


def extract_issuer_phrase_from_bond_name(value: str | None) -> str | None:
    text = _normalize_text(value)
    if not text:
        return None
    tokens: list[str] = []
    for token in text.split():
        if token in BOND_SUFFIX_TOKENS or _looks_like_issue_token(token):
            break
        tokens.append(token)
    phrase = normalize_issuer_name(" ".join(tokens))
    return phrase or None


def issuer_phrase_tokens(value: str | None) -> set[str]:
    return {
        token
        for token in normalize_issuer_name(value).split()
        if len(token) > 1 and not _looks_like_issue_token(token)
    }


def _normalize_text(value: str | None) -> str:
    text = str(value or "").casefold().strip()
    text = (
        text.replace("«", " ")
        .replace("»", " ")
        .replace('"', " ")
        .replace("'", " ")
        .replace("`", " ")
    )
    text = re.sub(r"[\.,;:()\[\]{}\\/|_]+", " ", text)
    text = re.sub(r"[-–—]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _looks_like_issue_token(value: str) -> bool:
    return bool(
        re.fullmatch(r"\d+[a-zа-я]?", value)
        or re.fullmatch(r"\d{2,}[a-zа-я0-9]*", value)
        or re.fullmatch(r"\d{3,}р.*", value)
        or re.fullmatch(r"[a-zа-я]*\d{2,}.*", value)
    )
