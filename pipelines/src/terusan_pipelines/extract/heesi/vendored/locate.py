"""Find the PDF page holding a table's data grid (not its TOC line)."""
from __future__ import annotations

import re

from .normalize import parse_year


class SectionNotFound(Exception):
    pass


_TOC_LINE_RE = re.compile(r"\.{2,}\s*\d+\s*$|\s\d{1,3}\s*$")


_PAGE_TEXT_CACHE: dict[int, list[str]] = {}


def _page_texts(pdf) -> list[str]:
    key = id(pdf)
    cached = _PAGE_TEXT_CACHE.get(key)
    if cached is None:
        cached = [(p.extract_text() or "") for p in pdf.pages]
        _PAGE_TEXT_CACHE[key] = cached
    return cached


def _looks_like_data_grid(text: str) -> bool:
    year_lines = 0
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parse_year(parts[0]) is not None:
            year_lines += 1
    return year_lines >= 3


def find_table_page(pdf, title_regex: str, *, chapter_hint: int | None = None) -> int:
    pat = re.compile(title_regex, re.IGNORECASE)
    candidates: list[int] = []
    for idx, text in enumerate(_page_texts(pdf)):
        if not pat.search(text):
            continue
        if _looks_like_data_grid(text):
            candidates.append(idx)
    if not candidates:
        raise SectionNotFound(title_regex)
    # Prefer the earliest data-grid page after the front matter.
    return candidates[0]
