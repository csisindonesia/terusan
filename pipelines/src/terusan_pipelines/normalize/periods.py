"""Reading the time a figure refers to.

Published statistics label periods in whatever the publisher favours:
`2026-01`, `Januari 2026`, `Triwulan I 2026`, `2026M01`, `Q1 2026`. Silver
stores one canonical label plus explicit start and end dates, because a period
string cannot be compared, joined or filtered — and half the questions asked of
a research warehouse are about ranges (program.md §10).

Start and end are both inclusive, so a monthly period ends on the last day of
its month rather than the first of the next. Half-open ranges are less
error-prone in code and more error-prone in a SQL console, and this data is
read far more often than it is written.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Resolution(StrEnum):
    """How coarse a period is."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMIANNUAL = "semiannual"
    ANNUAL = "annual"


#: Indonesian and English month names, plus the abbreviations that appear in
#: table headers. Indexed by the first letters that identify them uniquely.
_MONTHS: dict[str, int] = {
    "januari": 1,
    "january": 1,
    "jan": 1,
    "februari": 2,
    "february": 2,
    "feb": 2,
    "peb": 2,
    "pebruari": 2,
    "maret": 3,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "mei": 5,
    "may": 5,
    "juni": 6,
    "june": 6,
    "jun": 6,
    "juli": 7,
    "july": 7,
    "jul": 7,
    "agustus": 8,
    "august": 8,
    "agu": 8,
    "ags": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "oktober": 10,
    "october": 10,
    "okt": 10,
    "oct": 10,
    "november": 11,
    "nopember": 11,
    "nov": 11,
    "desember": 12,
    "december": 12,
    "des": 12,
    "dec": 12,
}

#: Roman numerals as used for quarters and semesters in Indonesian releases.
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}

_YEAR = r"(?P<year>(?:19|20)\d{2})"

_PATTERNS: list[tuple[re.Pattern, Resolution]] = [
    (
        re.compile(rf"^{_YEAR}[-/](?P<month>0?[1-9]|1[0-2])[-/](?P<day>0?[1-9]|[12]\d|3[01])$"),
        Resolution.DAILY,
    ),
    (
        re.compile(rf"^(?P<day>0?[1-9]|[12]\d|3[01])[-/](?P<month>0?[1-9]|1[0-2])[-/]{_YEAR}$"),
        Resolution.DAILY,
    ),
    (re.compile(rf"^{_YEAR}[-/]?[mM](?P<month>0?[1-9]|1[0-2])$"), Resolution.MONTHLY),
    (re.compile(rf"^{_YEAR}[-/](?P<month>0?[1-9]|1[0-2])$"), Resolution.MONTHLY),
    (re.compile(rf"^{_YEAR}[-/]?[qQ](?P<quarter>[1-4])$"), Resolution.QUARTERLY),
    (re.compile(rf"^[qQ](?P<quarter>[1-4])[-/ ]{_YEAR}$"), Resolution.QUARTERLY),
    (re.compile(rf"^{_YEAR}[-/]?[sS](?P<half>[12])$"), Resolution.SEMIANNUAL),
    (re.compile(rf"^{_YEAR}$"), Resolution.ANNUAL),
]

_TRIWULAN = re.compile(rf"^(?:triwulan|tw|kuartal)\s*(?P<q>[1-4]|i{{1,3}}|iv)\s*[-/ ]?\s*{_YEAR}$")
_SEMESTER = re.compile(rf"^(?:semester|smt)\s*(?P<h>[12]|i{{1,2}})\s*[-/ ]?\s*{_YEAR}$")
_MONTH_NAME = re.compile(rf"^(?P<name>[a-z]+)\.?\s+{_YEAR}$")
_NAME_MONTH_REVERSED = re.compile(rf"^{_YEAR}\s+(?P<name>[a-z]+)\.?$")


class UnparseablePeriod(ValueError):
    """A period label nothing recognised.

    Raised rather than guessed. A misread period silently attributes a figure
    to the wrong year, which no downstream check would catch.
    """


@dataclass(frozen=True, slots=True)
class Period:
    """One time period, canonically labelled and bounded."""

    label: str
    start: date
    end: date
    resolution: Resolution

    @property
    def year(self) -> int:
        return self.start.year

    def __contains__(self, day: date) -> bool:
        return self.start <= day <= self.end


def parse_period(raw: str) -> Period:
    """Read a published period label.

    Raises `UnparseablePeriod` rather than returning None, because a caller
    that forgets to check would otherwise write a null period and lose the
    figure's place in time.
    """
    text = (raw or "").strip().lower().replace(" ", " ")
    text = re.sub(r"\s+", " ", text)
    if not text:
        raise UnparseablePeriod("empty period label")

    if match := _TRIWULAN.match(text):
        quarter = _ROMAN.get(match.group("q")) or int(match.group("q"))
        return _quarter(int(match.group("year")), quarter)

    if match := _SEMESTER.match(text):
        half = _ROMAN.get(match.group("h")) or int(match.group("h"))
        return _half(int(match.group("year")), half)

    for pattern, resolution in _PATTERNS:
        if match := pattern.match(text):
            return _from_match(match, resolution)

    for pattern in (_MONTH_NAME, _NAME_MONTH_REVERSED):
        if match := pattern.match(text):
            month = _MONTHS.get(match.group("name"))
            if month:
                return _month(int(match.group("year")), month)

    raise UnparseablePeriod(f"unrecognised period label: {raw!r}")


def try_parse_period(raw: str) -> Period | None:
    """`parse_period` for callers that expect misses, such as column sniffing."""
    try:
        return parse_period(raw)
    except UnparseablePeriod:
        return None


def _from_match(match: re.Match, resolution: Resolution) -> Period:
    year = int(match.group("year"))
    groups = match.groupdict()

    if resolution is Resolution.DAILY:
        day = date(year, int(groups["month"]), int(groups["day"]))
        return Period(day.isoformat(), day, day, Resolution.DAILY)
    if resolution is Resolution.MONTHLY:
        return _month(year, int(groups["month"]))
    if resolution is Resolution.QUARTERLY:
        return _quarter(year, int(groups["quarter"]))
    if resolution is Resolution.SEMIANNUAL:
        return _half(year, int(groups["half"]))
    return Period(str(year), date(year, 1, 1), date(year, 12, 31), Resolution.ANNUAL)


def _month(year: int, month: int) -> Period:
    last = calendar.monthrange(year, month)[1]
    return Period(
        f"{year}-{month:02d}", date(year, month, 1), date(year, month, last), Resolution.MONTHLY
    )


def _quarter(year: int, quarter: int) -> Period:
    if not 1 <= quarter <= 4:
        raise UnparseablePeriod(f"quarter {quarter} is out of range")
    first = 3 * (quarter - 1) + 1
    last_month = first + 2
    return Period(
        f"{year}-Q{quarter}",
        date(year, first, 1),
        date(year, last_month, calendar.monthrange(year, last_month)[1]),
        Resolution.QUARTERLY,
    )


def _half(year: int, half: int) -> Period:
    if half not in (1, 2):
        raise UnparseablePeriod(f"semester {half} is out of range")
    return (
        Period(f"{year}-S1", date(year, 1, 1), date(year, 6, 30), Resolution.SEMIANNUAL)
        if half == 1
        else Period(f"{year}-S2", date(year, 7, 1), date(year, 12, 31), Resolution.SEMIANNUAL)
    )
