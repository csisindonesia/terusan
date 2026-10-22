"""Government content restriction reports into Bronze records.

Three publishers, three shapes, one record layout. Each figure becomes one
record naming the publisher's own label for it (`measure`), the raw period, the
raw country, and whatever breakdown the publisher gives — requester, product,
reason, platform — so the observatory's adapters can build observations from
Bronze alone, and so a reader can find the cell a figure came from by
`source_table` and `row_locator`.

What each reader knows:

- **Google** ships a zip of CSVs in two formats (see
  `sources/transparency/google.py`). The member names say which is which, and
  each is checked for the columns read from it: a column that disappears would
  otherwise yield no records for that measure, and a measure that stops
  arriving looks exactly like requests that stopped.
- **TikTok** ships one long CSV, a row per country, half-year and measure,
  with a byte-order mark on the header.
- **Komdigi** ships small workbooks — a label column and a figure — whose year
  is stated in the title or the filename and nowhere in the cells. The
  2018–2021 website table covers January 2018 to October 2021 as one figure per
  category, which no calendar period in Silver can hold, so it is read with its
  range and kept to the observatory's tables.

Every country is kept, not only Indonesia: the observatory compares Indonesia
with its neighbours, like for like, and the comparison needs their rows.

Records carrying an `indicator` are the as-published country figures Silver
can hold — TikTok's per-country measures, Google's pre-2019 country totals,
Komdigi's annual per-platform counts — and they are in their own Bronze
datasets so that `scripts/normalize-transparency.sh` reads nothing else.
Google's 2019-onward rows are Silver-bound too, as a breakdown with a category
per requester, product and reason — never as a sum, which is not a figure
Google stated. Google's pre-2019 breakdowns stay in `government-removals-detail`.

An empty cell yields no record. A missing marker — Komdigi's `-` — does, with
the marker as its value: it is the publisher saying the figure is unavailable,
which Silver records as a status and never as zero.
"""

from __future__ import annotations

import calendar
import csv
import io
import re
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from typing import Any

from ..observatory import taxonomy as tx
from .archives import read_member
from .base import ExtractionError, Extractor, Landed
from .tabular import decode

GOOGLE_SLUG = "google-government-removals"
TIKTOK_SLUG = "tiktok-government-removals"
KOMDIGI_SLUG = "komdigi-content-handling"

#: Bronze datasets. The unsuffixed ones hold Silver-bound records only.
GOOGLE_TOTALS = "government-removals-totals"
GOOGLE_DETAIL = "government-removals-detail"
#: Google's 2019-onward rows as published, one figure per requester, product
#: and reason: Silver-bound, with the three as a category, and never summed.
GOOGLE_BREAKDOWN = "government-removals-breakdown"
TIKTOK_COUNTRIES = "government-removal-requests"
TIKTOK_WORLDWIDE = "government-removal-requests-worldwide"
KOMDIGI_SOCIAL = "content-handling"
KOMDIGI_WEBSITES = "content-handling-websites"

#: Markers a publisher writes for a figure it does not have. Kept as written.
MISSING_MARKERS = frozenset({"-", "–", "x", "...", "…", "n/a", "na"})

_GOOGLE_HALF = re.compile(
    r"^(?P<from>January|July)\s*[-–]\s*(?P<to>June|December)\s+(?P<year>\d{4})$", re.I
)
_TIKTOK_HALF = re.compile(r"^(?P<from>Jan|Jul)\s*[-–]\s*(?P<to>Jun|Dec)\s+(?P<year>\d{4})$", re.I)
_ISO_DATE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})$")

_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            (),
            ("januari", "january", "jan"),
            ("februari", "february", "feb", "pebruari"),
            ("maret", "march", "mar"),
            ("april", "apr"),
            ("mei", "may"),
            ("juni", "june", "jun"),
            ("juli", "july", "jul"),
            ("agustus", "august", "aug", "agu"),
            ("september", "sep", "sept"),
            ("oktober", "october", "okt", "oct"),
            ("november", "nopember", "nov"),
            ("desember", "december", "des", "dec"),
        )
    )
    for name in names
}
_MONTH_YEAR = re.compile(r"(?P<month>[a-z]+)[-_\s]+(?P<year>(?:19|20)\d{2})", re.I)
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


# -- periods ---------------------------------------------------------------


def half_year(year: int, half: int) -> dict[str, str]:
    start, end = (
        (date(year, 1, 1), date(year, 6, 30))
        if half == 1
        else (date(year, 7, 1), date(year, 12, 31))
    )
    return {
        "period": f"{year}-S{half}",
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
    }


def google_period(raw: str) -> dict[str, str] | None:
    """Google's "January - June 2025", or a pre-2019 period-ending date."""
    raw = raw.strip()
    match = _GOOGLE_HALF.match(raw)
    if match:
        return half_year(int(match["year"]), 1 if match["from"].lower() == "january" else 2)
    match = _ISO_DATE.match(raw)
    if match and (match["month"], match["day"]) in {("06", "30"), ("12", "31")}:
        return half_year(int(match["year"]), 1 if match["month"] == "06" else 2)
    return None


def tiktok_period(raw: str) -> dict[str, str] | None:
    match = _TIKTOK_HALF.match(raw.strip())
    if not match:
        return None
    return half_year(int(match["year"]), 1 if match["from"].lower() == "jan" else 2)


def stated_period(*texts: str | None) -> dict[str, str] | None:
    """The period a Komdigi title or filename states.

    A range — "periode Januari 2018 – Oktober 2021" — where two months are
    named, read first because its years would otherwise read as two years.
    Otherwise the one year it names, as a calendar year. Two bare years, or
    none, is not a period this can state, and returns nothing rather than a
    guess.
    """
    for text in texts:
        if not text:
            continue
        spaced = text.replace("_", " ")
        pairs = [
            (_MONTHS[m["month"].lower()], int(m["year"]))
            for m in _MONTH_YEAR.finditer(spaced)
            if m["month"].lower() in _MONTHS
        ]
        if len(pairs) >= 2:
            (m1, y1), (m2, y2) = pairs[0], pairs[-1]
            start = date(y1, m1, 1)
            end = date(y2, m2, calendar.monthrange(y2, m2)[1])
            if start <= end:
                return {
                    "period": "",
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                }
        years = sorted(set(_YEAR.findall(spaced)))
        if len(years) == 1:
            year = int(years[0])
            return {
                "period": str(year),
                "period_start": date(year, 1, 1).isoformat(),
                "period_end": date(year, 12, 31).isoformat(),
            }
    return None


# -- shared ----------------------------------------------------------------


def _cell_text(value: Any) -> str:
    """A cell as text, with a spreadsheet's integral float written as an integer."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def tiktok_number(raw: str) -> str:
    """TikTok writes counts as `1113.0`; the `.0` is pandas, not precision."""
    text = raw.strip()
    try:
        number = Decimal(text)
    except InvalidOperation:
        return text
    if number == number.to_integral_value() and "e" not in text.lower():
        return str(int(number))
    return text


def _check_columns(
    path: str, member: str, present: Iterable[str] | None, wanted: Sequence[str]
) -> None:
    missing = [c for c in wanted if c not in set(present or ())]
    if missing:
        raise ExtractionError(
            path,
            f"{member} is missing {', '.join(missing)}; the publisher has reshaped the "
            "file and the measures must be read again",
        )


def _record(
    measure: tx.Measure,
    value: str,
    *,
    provider: str,
    source_table: str,
    row_locator: str,
    period_raw: str,
    period: dict[str, str] | None,
    country_raw: str,
    publisher: str,
    with_indicator: bool,
    **breakdown: str,
) -> dict[str, str]:
    code = tx.country_code(country_raw) if country_raw else None
    return {
        "provider": provider,
        "source_table": source_table,
        "row_locator": row_locator,
        "measure": measure.label,
        "value": value,
        "unit": measure.unit.value,
        "period_raw": period_raw,
        **(period or {"period": "", "period_start": "", "period_end": ""}),
        "country_raw": country_raw,
        "country_code": code or "",
        # What Silver resolves: the ISO code where one is known, which every
        # country in the reference carries as an alias, and the name otherwise
        # — preserved unresolved rather than matched to a guess.
        "geo": code or country_raw,
        "indicator": (measure.indicator or "") if with_indicator else "",
        "series_name": (measure.series_name or "") if with_indicator else "",
        "publisher": publisher,
        **{k: v for k, v in breakdown.items()},
    }


# -- Google ----------------------------------------------------------------

_GOOGLE_CURRENT = "google-government-removal-requests.csv"
_GOOGLE_HIST_TOTALS = "google-government-removal-requests-historical.csv"
_GOOGLE_HIST_DETAIL = "google-government-detailed-removal-requests-historical.csv"
_GOOGLE_HIST_TOTAL_ONLY = "google-government-detailed-removal-requests.csv"


class GoogleRemovalsExtractor(Extractor):
    """Every figure in Google's government removals archive."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == GOOGLE_SLUG and landed.path.suffix.lower() == ".zip"

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        path = str(landed.path)
        try:
            archive = zipfile.ZipFile(io.BytesIO(landed.path.read_bytes()))
        except zipfile.BadZipFile as exc:
            raise ExtractionError(path, f"unreadable archive: {exc}") from exc

        with archive:
            members = {
                PurePosixPath(n).as_posix(): n
                for n in archive.namelist()
                if n.lower().endswith(".csv") and not n.startswith("__MACOSX/")
            }
            # Which format a file is in is said by its folder and name, which
            # is how Google's README tells them apart.
            readers = []
            for posix, name in sorted(members.items()):
                base = PurePosixPath(posix).name
                historical = "/historical/" in f"/{posix}"
                if base == _GOOGLE_CURRENT and not historical:
                    readers.append((name, self._current))
                elif base == _GOOGLE_HIST_TOTALS:
                    readers.append((name, self._historical_totals))
                elif base == _GOOGLE_HIST_DETAIL:
                    readers.append((name, self._historical_detail))
                elif base == _GOOGLE_HIST_TOTAL_ONLY and historical:
                    readers.append((name, self._historical_total_only))
            if not any(r[1] == self._current for r in readers):
                raise ExtractionError(
                    path, f"archive holds no {_GOOGLE_CURRENT}; Google has reshaped it"
                )

            number = 0
            for name, reader in readers:
                rows = csv.DictReader(io.StringIO(decode(read_member(archive, name, path))))
                for dataset, record in reader(path, name, rows):
                    number += 1
                    yield {"dataset": dataset, "row_number": number, "columns": record}

    def _current(
        self, path: str, member: str, rows: csv.DictReader
    ) -> Iterator[tuple[str, dict[str, str]]]:
        _check_columns(
            path,
            member,
            rows.fieldnames,
            ["time_period", "country", "cldr_territory", "requestor", "product", "reason"]
            + [m.label for m in tx.GOOGLE_DETAIL_MEASURES],
        )
        table = PurePosixPath(member).name
        for line, row in enumerate(rows, start=2):
            raw_period = (row.get("time_period") or "").strip()
            requester = (row.get("requestor") or "").strip()
            product = (row.get("product") or "").strip()
            reason = (row.get("reason") or "").strip()
            for measure in tx.GOOGLE_DETAIL_MEASURES:
                value = (row.get(measure.label) or "").strip()
                if not value:
                    continue
                yield (
                    GOOGLE_BREAKDOWN,
                    _record(
                        measure,
                        value,
                        provider="google",
                        source_table=table,
                        row_locator=f"{table}#L{line}",
                        period_raw=raw_period,
                        period=google_period(raw_period),
                        country_raw=(row.get("cldr_territory") or row.get("country") or "").strip(),
                        publisher="Google",
                        with_indicator=True,
                        country_name=(row.get("country") or "").strip(),
                        requester_raw=requester,
                        product_raw=product,
                        reason_raw=reason,
                        # The three together are what make a row one figure
                        # rather than a share of one, so they are Silver's
                        # category for it.
                        category=" · ".join(
                            p or "unspecified" for p in (requester, product, reason)
                        ),
                        format="2019",
                    ),
                )

    def _historical_totals(
        self, path: str, member: str, rows: csv.DictReader
    ) -> Iterator[tuple[str, dict[str, str]]]:
        _check_columns(
            path,
            member,
            rows.fieldnames,
            ["Period Ending", "Country/Region", "CLDR Territory Code"]
            + [m.label for m in tx.GOOGLE_TOTAL_MEASURES],
        )
        table = f"historical/{PurePosixPath(member).name}"
        for line, row in enumerate(rows, start=2):
            raw_period = (row.get("Period Ending") or "").strip()
            for measure in tx.GOOGLE_TOTAL_MEASURES:
                value = (row.get(measure.label) or "").strip()
                if not value:
                    continue
                yield (
                    GOOGLE_TOTALS,
                    _record(
                        measure,
                        value,
                        provider="google",
                        source_table=table,
                        row_locator=f"{table}#L{line}",
                        period_raw=raw_period,
                        period=google_period(raw_period),
                        country_raw=(
                            row.get("CLDR Territory Code") or row.get("Country/Region") or ""
                        ).strip(),
                        publisher="Google",
                        with_indicator=True,
                        country_name=(row.get("Country/Region") or "").strip(),
                        requester_raw=measure.label.split(":", 1)[0],
                        format="2009",
                    ),
                )

    def _historical_detail(
        self, path: str, member: str, rows: csv.DictReader
    ) -> Iterator[tuple[str, dict[str, str]]]:
        yield from self._historical_breakdown(
            path, member, rows, tx.GOOGLE_HISTORICAL_DETAIL_MEASURES
        )

    def _historical_total_only(
        self, path: str, member: str, rows: csv.DictReader
    ) -> Iterator[tuple[str, dict[str, str]]]:
        yield from self._historical_breakdown(
            path, member, rows, tx.GOOGLE_HISTORICAL_TOTAL_MEASURES
        )

    def _historical_breakdown(
        self, path: str, member: str, rows: csv.DictReader, measures: Sequence[tx.Measure]
    ) -> Iterator[tuple[str, dict[str, str]]]:
        _check_columns(
            path,
            member,
            rows.fieldnames,
            ["Period Ending", "Country/Region", "CLDR Territory Code", "Product", "Reason"]
            + [m.label for m in measures],
        )
        table = f"historical/{PurePosixPath(member).name}"
        for line, row in enumerate(rows, start=2):
            raw_period = (row.get("Period Ending") or "").strip()
            for measure in measures:
                value = (row.get(measure.label) or "").strip()
                if not value:
                    continue
                yield (
                    GOOGLE_DETAIL,
                    _record(
                        measure,
                        value,
                        provider="google",
                        source_table=table,
                        row_locator=f"{table}#L{line}",
                        period_raw=raw_period,
                        period=google_period(raw_period),
                        country_raw=(
                            row.get("CLDR Territory Code") or row.get("Country/Region") or ""
                        ).strip(),
                        publisher="Google",
                        with_indicator=False,
                        country_name=(row.get("Country/Region") or "").strip(),
                        requester_raw=measure.label
                        if measure.requester is not tx.RequesterType.ALL
                        else "",
                        product_raw=(row.get("Product") or "").strip(),
                        reason_raw=(row.get("Reason") or "").strip(),
                        format="2009",
                    ),
                )


# -- TikTok ----------------------------------------------------------------

_TIKTOK_COLUMNS = ("metric_name", "period", "period_value", "location", "location_value", "result")
_TIKTOK_MEASURES = {m.label: m for m in tx.TIKTOK_MEASURES}


class TikTokRemovalsExtractor(Extractor):
    """Every figure in TikTok's government removal requests CSV."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == TIKTOK_SLUG and landed.path.suffix.lower() == ".csv"

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        path = str(landed.path)
        text = decode(landed.path.read_bytes()).lstrip("﻿")
        rows = csv.DictReader(io.StringIO(text))
        _check_columns(path, landed.path.name, rows.fieldnames, _TIKTOK_COLUMNS)
        table = landed.original_filename or landed.path.name

        number = 0
        for line, row in enumerate(rows, start=2):
            label = (row.get("metric_name") or "").strip()
            value = tiktok_number(row.get("result") or "")
            if not label or not value:
                continue
            raw_period = (row.get("period_value") or "").strip()
            worldwide = (row.get("location") or "").strip() == "all"
            # A label nobody has mapped still lands, as `other`, so the
            # adapter can quarantine it by name; dropping it here would leave
            # nothing to say TikTok added a measure.
            measure = _TIKTOK_MEASURES.get(label) or tx.Measure(label, tx.Metric.OTHER)
            number += 1
            yield {
                "dataset": TIKTOK_WORLDWIDE if worldwide else TIKTOK_COUNTRIES,
                "row_number": number,
                "columns": _record(
                    measure,
                    value,
                    provider="tiktok",
                    source_table=table,
                    row_locator=f"{table}#L{line}",
                    period_raw=raw_period,
                    period=tiktok_period(raw_period)
                    if (row.get("period") or "").strip() == "half-year"
                    else None,
                    country_raw="" if worldwide else (row.get("location_value") or "").strip(),
                    publisher="TikTok",
                    with_indicator=not worldwide,
                    # TikTok's own breakdown columns, always "all" in the
                    # releases read so far — kept so the day they are not,
                    # the rows are not silently merged.
                    policy=(row.get("policy_value") or "").strip(),
                    task=(row.get("task_value") or "").strip(),
                    published_date=(row.get("date") or "").strip(),
                ),
            }
        if number == 0:
            raise ExtractionError(path, "no figures in the file; TikTok has reshaped it")


# -- Komdigi ---------------------------------------------------------------


class KomdigiContentExtractor(Extractor):
    """Komdigi's content handling and website blocking workbooks."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == KOMDIGI_SLUG and landed.path.suffix.lower() == ".xlsx"

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        from .workbooks import _load

        path = str(landed.path)
        extra = landed.extra or {}
        filename = landed.original_filename or landed.path.name
        title = str(extra.get("resource_name") or "")
        dataset_title = str(extra.get("dataset_title") or "")
        websites = "situs" in f"{filename} {title}".lower()
        period = stated_period(title, filename, dataset_title)
        if period is None:
            raise ExtractionError(path, "neither the title nor the filename states the period")

        try:
            workbook = _load(landed.path.read_bytes())
        except Exception as exc:  # openpyxl raises a zoo of types on a bad file
            raise ExtractionError(path, f"unreadable workbook: {exc}") from exc

        measure = tx.KOMDIGI_WEBSITE_MEASURE if websites else tx.KOMDIGI_SOCIAL_MEASURE
        number = 0
        for sheet in workbook.worksheets:
            for line, cells in enumerate(sheet.iter_rows(values_only=True), start=1):
                filled = [(i, _cell_text(c)) for i, c in enumerate(cells) if _cell_text(c)]
                if len(filled) < 2:
                    continue  # a title row, or a blank one
                (_, value) = filled[-1]
                labels = [text for i, text in filled[:-1] if not _is_number(text)]
                if not labels:
                    continue
                label = labels[-1]
                if not (_is_number(value) or value.lower() in MISSING_MARKERS):
                    continue  # the header row: "Media Sosial | Jumlah"
                total = label.lower().startswith(("total", "jumlah total"))
                platform = "" if websites or total else tx.platform_slug(label)
                number += 1
                yield {
                    "dataset": KOMDIGI_WEBSITES
                    if websites or not period["period"]
                    else KOMDIGI_SOCIAL,
                    "row_number": number,
                    "columns": _record(
                        measure,
                        value,
                        provider="komdigi",
                        source_table=f"{filename}!{sheet.title}",
                        row_locator=f"{filename}!{sheet.title}!R{line}",
                        period_raw=title or filename,
                        period=period,
                        country_raw="ID",
                        publisher="Kementerian Komunikasi dan Digital",
                        with_indicator=not websites and not total and bool(period["period"]),
                        label_raw=label,
                        platform_raw="" if websites else label,
                        platform=platform,
                        reason_raw=label if websites and not total else "",
                        row_role="total" if total else "component",
                        dataset_title=dataset_title,
                    )
                    | (
                        {
                            "indicator": tx.komdigi_indicator(platform),
                            "series_name": "Komdigi: negative content handled on "
                            + tx.platform_name(platform, label),
                        }
                        if not websites and not total and period["period"]
                        else {}
                    ),
                }
        if number == 0:
            raise ExtractionError(path, "no figures found in the workbook")


def _is_number(text: str) -> bool:
    try:
        Decimal(text.replace(",", ""))
    except InvalidOperation:
        return False
    return True
