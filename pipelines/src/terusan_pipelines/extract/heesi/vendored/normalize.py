"""Parsing helpers for values and labels lifted from HEESI PDF tables."""
from __future__ import annotations

import re

_BLANKS = {"", "-", "–", "—", "n.a", "n.a.", "na", "n/a", "..", "…"}
_FOOTNOTE_RE = re.compile(r"\s*(?:\d\)|\*+|\d\])\s*$")
_HYPHEN_BREAK_RE = re.compile(r"(\w)-\s*\n\s*(\w)")
_WS_RE = re.compile(r"\s+")
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
MIN_YEAR, MAX_YEAR = 2000, 2035


def parse_number(text):
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip()
    if s.lower() in _BLANKS:
        return None
    s = s.replace("−", "-").replace("\xa0", "")
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    if s.endswith("-"):
        neg, s = True, s[:-1]
    s = s.replace(",", "")
    if s.startswith("-"):
        neg, s = True, s[1:]
    try:
        val = float(s)
    except ValueError:
        return None
    return -val if neg else val


def clean_label(text: str) -> str:
    if text is None:
        return ""
    s = _HYPHEN_BREAK_RE.sub(r"\1\2", str(text))
    s = s.replace("\n", " ")
    s = _FOOTNOTE_RE.sub("", s)
    s = _WS_RE.sub(" ", s).strip()
    s = re.sub(r"\s*\*+\s*$", "", s).strip()
    return s


def parse_year(text) -> int | None:
    if text is None:
        return None
    s = str(text)
    s = _FOOTNOTE_RE.sub("", s).strip()
    m = _YEAR_RE.search(s)
    if not m:
        return None
    year = int(m.group(1))
    if MIN_YEAR <= year <= MAX_YEAR:
        return year
    return None
