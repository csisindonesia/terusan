"""Trading Economics indicator pages into Bronze records.

The generic HTML extractor would land these as prose: one document per page
holding the whole layout as text, with the figures buried in a sentence. The
numbers are in three tables, and reading them is this extractor's whole job.

A detail page yields two kinds of row:

- `reading` — the page's own latest print, taken from the table of
  neighbouring indicators, where the page lists itself among them. This is the
  precise figure: the index page rounds to three significant digits, printing
  Indonesia's housing index as `111` where its own page says `110.89`, and a
  warehouse that keeps the rounded one has thrown away two digits it was
  given. Unlike the summary block it is dated — the neighbours table prints
  the reference period beside each figure.
- `summary` — the indicator's own headline figures: latest, previous, all-time
  high and low, the years they span, the unit and the release frequency.
- `calendar` — one row per release, with the reference period, what was
  reported, what was reported before it, and the market consensus.
- `rating` — the credit rating page publishes no figure at all: it is a table
  of agencies, their rating, the outlook and the date they said so.

and the index page yields one `index` row per indicator, which is the whole
country's position in a single artifact. That makes the index worth keeping
even though the detail pages repeat it: if a detail page 404s on the day an
indicator is renamed, the index still carries the figure.

Every row carries the country the page is for, read off its URL rather than
assumed: Trading Economics publishes the same page shape for 196 countries, and
a row that does not say which one cannot be resolved to a place later.

Every row also carries the identifier the series is published under and the
name it is published by, both composed from the page's own slug. Composing the
identifier here is deliberate: it is the extractor that still has the source's
URLs and titles in hand, and a second place to build one is a second place for
it to be built differently.

Nothing else is interpreted here. "146500.00" stays a string, "Usd - Juta" stays as
written, and an empty cell stays empty — Bronze does not decide what a value
means (program.md §6).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from html import unescape
from typing import Any
from urllib.parse import urlparse

from .base import Extractor, Landed
from .tabular import decode

SOURCE_SLUG = "tradingeconomics-indonesia"

#: The dataset the crawl's index page lands under, mirrored from the source.
INDEX_DATASET = "indonesia-indicators"

#: The country this source covers, where a landed artifact carries no URL to
#: read it from. The source fetches Indonesia and nothing else.
DEFAULT_COUNTRY = "Indonesia"

_TABLE = re.compile(r"<table\b([^>]*)>(.*?)</table>", re.IGNORECASE | re.DOTALL)
_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_HREF = re.compile(r"href=['\"]([^'\"]+)['\"]", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")

#: The summary table is the one whose header names the release frequency —
#: no id, no stable class, but the only table on the page carrying that column.
_SUMMARY_HEADERS = ("realisasi", "frekuensi")

#: Column order of the summary table, as the page prints it. The first cell is
#: the indicator's name and is empty on a detail page, so it is dropped.
SUMMARY_COLUMNS = (
    "actual",
    "previous",
    "highest",
    "lowest",
    "period",
    "unit",
    "frequency",
)

#: Column order of a calendar row. The third cell holds the indicator name in
#: a hidden div, which is how the site labels a row for its mobile layout.
CALENDAR_COLUMNS = (
    "released_on",
    "released_at_gmt",
    "indicator_name",
    "reference",
    "actual",
    "previous",
    "consensus",
)

#: Column order of an index row, after the indicator's name and link.
INDEX_COLUMNS = ("last", "previous", "highest", "lowest", "unit", "reference")

#: Column order of the credit rating table, which is in English on an otherwise
#: Indonesian page.
RATING_COLUMNS = ("agency", "rating", "outlook", "rated_on")

#: The rating table's header, by which it is recognised.
_RATING_HEADERS = ("agency", "rating", "outlook")

#: Column order of the neighbours table, after the indicator's name and link.
#: The same four facts the index prints, at the precision the page holds them.
READING_COLUMNS = ("last", "previous", "unit", "reference")

#: The neighbours table is the one whose header names both the latest figure
#: and the period it belongs to.
_READING_HEADERS = ("terakhir", "referensi")

#: The vendor prefix every identifier from this source carries.
#:
#: Trading Economics republishes Bank Indonesia's and BPS's figures at its own
#: revision and rounding. An unprefixed `inflation_cpi` would eventually
#: collide with the same series taken from the agency that publishes it, and
#: the two are not interchangeable — one is the vendor's reading of the other.
INDICATOR_PREFIX = "te"

#: Words in a slug that are abbreviations rather than words, with the casing
#: they are actually written in. Without this, `gdp-growth` titles as `Gdp
#: Growth`, which is not a thing anyone writes.
_ACRONYMS = {
    "cpi": "CPI",
    "gdp": "GDP",
    "gni": "GNI",
    "m0": "M0",
    "m1": "M1",
    "m2": "M2",
    "m3": "M3",
    "mom": "MoM",
    "pmi": "PMI",
    "ppp": "PPP",
    "qoq": "QoQ",
    "yoy": "YoY",
}

#: Words a title leaves lowercase unless they open it.
_MINOR = frozenset({"and", "for", "in", "of", "per", "to"})


def indicator_key(slug: str) -> str:
    """The key a page's series is published under — `te_housing_index`.

    The readable key, not the identifier: Silver derives a code from this and
    publishes the series under that (program.md §10). What matters here is
    that the key is stable, because it is what a re-normalized series lands
    back on, and that it names the vendor.
    """
    return f"{INDICATOR_PREFIX}_{slug.replace('-', '_')}"


def indicator_title(slug: str) -> str:
    """What to call the series — `housing-index` becomes `Housing Index`.

    Taken from the URL rather than from the page's heading because the URL is
    the English name of the series on every language edition, and this is the
    Indonesian one: its tables say "Indeks Perumahan", which is the right name
    in the wrong language for a portal that reads in English. The Indonesian
    name is kept beside it, as the site printed it.

    Derived rather than looked up. A hundred hand-written titles would be a
    hundred things to keep true as Trading Economics adds and retires pages,
    and the slug is what the vendor itself calls the series.
    """
    words = [word for word in slug.split("-") if word]
    if not words:
        return slug

    titled = []
    for index, word in enumerate(words):
        if word in _ACRONYMS:
            titled.append(_ACRONYMS[word])
        elif index > 0 and word in _MINOR:
            titled.append(word)
        else:
            titled.append(word[:1].upper() + word[1:])
    return " ".join(titled)


def text(fragment: str) -> str:
    """A cell as Bronze holds it: the text, unescaped, whitespace collapsed."""
    return _SPACE.sub(" ", unescape(_TAG.sub(" ", fragment))).strip()


def country_of(source_url: str | None) -> str:
    """The country a page belongs to, from its URL.

    `https://id.tradingeconomics.com/indonesia/inflation-cpi` is Indonesia's —
    the first path segment names the country, in English, on every language
    edition of the site. Read rather than assumed so that widening the source
    beyond Indonesia does not quietly label another country's figures as
    Indonesian.
    """
    if not source_url:
        return DEFAULT_COUNTRY
    parts = [part for part in urlparse(source_url).path.split("/") if part]
    if not parts:
        return DEFAULT_COUNTRY
    return parts[0].replace("-", " ").title()


def _cells(row: str) -> list[str]:
    return [text(cell) for cell in _CELL.findall(row)]


def _is_header(row: str) -> bool:
    """Whether a row is the table's header.

    Checked on the cells rather than on `<thead>`: the calendar table closes
    its header `<th>` with a `</td>`, which leaves the parser no reliable
    section boundary but does leave every header cell a `<th>`.
    """
    return "<th" in row.lower()


def _link(row: str) -> str:
    match = _HREF.search(row)
    return match.group(1) if match else ""


class TradingEconomicsExtractor(Extractor):
    """Indicator pages into Bronze records."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == SOURCE_SLUG and landed.path.suffix.lower() in {
            ".html",
            ".htm",
        }

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        html = decode(landed.path.read_bytes())
        dataset = landed.dataset or SOURCE_SLUG
        country = country_of(landed.source_url)

        rows = (
            self._index_rows(html) if dataset == INDEX_DATASET else self._detail_rows(html, dataset)
        )
        for number, columns in enumerate(rows, start=1):
            slug = columns.get("indicator", "")
            yield {
                # A reading is filed under the index's dataset rather than
                # under the page it was read from. It is the same statement
                # the index makes about the same series, at full precision,
                # and one collection of Indonesian indicators is what Trading
                # Economics publishes — not a hundred collections of one
                # series each, which is what the catalogue would show if every
                # page's reading arrived as its own dataset.
                "dataset": INDEX_DATASET if columns.get("kind") == "reading" else dataset,
                "row_number": number,
                "columns": {
                    "country": country,
                    **({"indicator_key": indicator_key(slug)} if slug else {}),
                    **({"indicator_title": indicator_title(slug)} if slug else {}),
                    **columns,
                },
            }

    # -- index ------------------------------------------------------------

    def _index_rows(self, html: str) -> Iterator[dict[str, str]]:
        """One row per indicator, across the index's dozen category tables.

        The category a table sits under is not captured: it is printed outside
        the table in a heading the page rewrites freely, and the detail pages
        carry the same grouping in a form that does not move.
        """
        seen: set[str] = set()

        for _, body in _TABLE.findall(html):
            for row in _ROW.findall(body):
                if _is_header(row):
                    continue
                href = _link(row)
                if "/indonesia/" not in href:
                    continue

                cells = _cells(row)
                if len(cells) < 2:
                    continue

                slug = href.rsplit("/", 1)[-1]
                # An indicator listed in two categories — as the headline ones
                # are — is one reading, not two.
                if slug in seen:
                    continue
                seen.add(slug)

                columns = {
                    "kind": "index",
                    "indicator": slug,
                    "indicator_name": cells[0],
                    "url": href,
                }
                columns.update(_zip_cells(INDEX_COLUMNS, cells[1:]))
                yield columns

    # -- detail -----------------------------------------------------------

    def _detail_rows(self, html: str, dataset: str) -> Iterator[dict[str, str]]:
        summary_seen = False
        reading_seen = False

        for attributes, body in _TABLE.findall(html):
            rows = _ROW.findall(body)
            if not rows:
                continue

            if 'id="calendar"' in attributes.lower():
                yield from self._calendar_rows(rows, dataset)
                continue

            header = " ".join(_cells(rows[0])).lower()

            if all(word in header for word in _RATING_HEADERS):
                yield from self._rating_rows(rows[1:], dataset)
                continue

            if not summary_seen and all(word in header for word in _SUMMARY_HEADERS):
                summary_seen = True
                yield from self._summary_rows(rows[1:], dataset)
                continue

            # Everything else lists the indicator's neighbours in the same
            # category, and the page lists itself among them. Its own row is
            # taken — it is this page's reading, printed at full precision and
            # dated with the period it belongs to, which is the one thing the
            # summary block above cannot say. The neighbours are left alone:
            # each of those has a page of its own in the same run, and landing
            # them here would put one reading in the lake a dozen times over,
            # dated by whichever page was fetched.
            #
            # One page — the manufacturing PMI — carries nothing else: no
            # summary block, and not even its own row among the neighbours,
            # because the series is licensed from S&P Global and Trading
            # Economics shows it only to subscribers. It yields no rows, and
            # its reading comes from the index instead, which is one of the
            # reasons the index is landed as an artifact of its own.
            #
            # Recognised by its header, like the summary block: two pages —
            # the currency and the stock market — print a live quote table in
            # the same place, whose fourth column is the day's change rather
            # than the period, and reading one as the other would date a
            # figure by "0.45%".
            if not reading_seen and all(word in header for word in _READING_HEADERS):
                for row in self._reading_rows(rows, dataset):
                    reading_seen = True
                    yield row

    def _reading_rows(self, rows: list[str], dataset: str) -> Iterator[dict[str, str]]:
        """The page's own row among its neighbours: one reading, dated.

        Matched on the link rather than on the name, because the name is in
        Indonesian and the page's heading is not the only place it is written
        differently. The link is the slug the page was landed under.
        """
        for row in rows:
            if _is_header(row):
                continue
            if _link(row).rstrip("/").rsplit("/", 1)[-1] != dataset:
                continue

            cells = _cells(row)
            if len(cells) < 2:
                continue

            columns = {"kind": "reading", "indicator": dataset, "indicator_name": cells[0]}
            columns.update(_zip_cells(READING_COLUMNS, cells[1:]))
            yield columns
            return

    def _summary_rows(self, rows: list[str], dataset: str) -> Iterator[dict[str, str]]:
        for row in rows:
            if _is_header(row):
                continue
            cells = _cells(row)
            if len(cells) < 2:
                continue
            columns = {"kind": "summary", "indicator": dataset}
            # The leading cell names the indicator, and is blank on its own
            # page — the heading above the table has already said it.
            columns.update(_zip_cells(SUMMARY_COLUMNS, cells[1:]))
            yield columns

    def _rating_rows(self, rows: list[str], dataset: str) -> Iterator[dict[str, str]]:
        """One row per rating a agency has published, newest first."""
        for row in rows:
            if _is_header(row):
                continue
            cells = _cells(row)
            if len(cells) < 2 or not cells[0]:
                continue
            columns = {"kind": "rating", "indicator": dataset}
            columns.update(_zip_cells(RATING_COLUMNS, cells))
            yield columns

    def _calendar_rows(self, rows: list[str], dataset: str) -> Iterator[dict[str, str]]:
        for row in rows:
            if _is_header(row):
                continue
            cells = _cells(row)
            # The "next release" row, which the site leaves without a date.
            if len(cells) < len(CALENDAR_COLUMNS) - 2 or not cells[0]:
                continue
            columns = {"kind": "calendar", "indicator": dataset}
            columns.update(_zip_cells(CALENDAR_COLUMNS, cells))
            yield columns


def _zip_cells(names: tuple[str, ...], cells: list[str]) -> dict[str, str]:
    """Name the cells, tolerating a column the page has added or dropped.

    Extra cells are kept under their position rather than discarded: a column
    that appears upstream should show up in Bronze as an unnamed value to be
    investigated, not vanish silently.
    """
    named = {name: cells[index] if index < len(cells) else "" for index, name in enumerate(names)}
    for index in range(len(names), len(cells)):
        if cells[index]:
            named[f"column_{index + 1}"] = cells[index]
    return named
