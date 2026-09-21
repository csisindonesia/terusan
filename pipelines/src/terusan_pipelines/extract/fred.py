"""FRED series CSVs into Bronze records.

The generic CSV reader cannot serve this source. A FRED download has two
columns — `observation_date` and the series identifier — so the column holding
the figures is named differently in every file, and a Silver mapping keyed on a
column name would need seven hundred of them. It also states nothing else: not
the title, not the units, not the frequency. All of that was read off the
search listing at landing time and travels in the sidecar, so this extractor's
job is to put it back beside each figure.

Two derived columns are written, and both are worth being explicit about:

- `period` is the date restated at the frequency FRED declares for the series.
  A quarterly figure is dated `1952-04-01` in the file, which reads as a single
  day; `1952-Q2` is the period it actually refers to. The published date is
  kept in `date` alongside it, so the restatement can be checked or redone.
- `indicator` is the identifier the series will carry in Silver: eight
  characters, derived from the FRED series id. FRED's titles are too long to
  put in a URL and too alike to shorten — a hundred of them differ only in
  their last few words — so the identifier is a code and the title is carried
  as the indicator's name instead. Deriving it here rather than in the
  normalization script is what lets one generic command normalize every series
  in the dataset: the column names the indicator, so nothing has to be declared
  per series.

The series pages land here too, as one record each rather than one per
observation. They are where FRED states who publishes the figures, which
release they arrive in and what the series counts — a paragraph of prose that
would otherwise be copied onto every one of the quarter-million observation
rows to say the same thing six hundred times.

Nothing else is interpreted. "6261.0000000" stays a string, "." stays a dot,
and an empty cell stays empty — Bronze does not decide what a value means
(program.md §6).
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterator
from html import unescape
from typing import Any

from ..identifiers import short_id
from .base import Extractor, Landed
from .tabular import decode

#: Every FRED source shares the sidecar shape, so the extractor claims them by
#: prefix rather than being duplicated per crawl.
SOURCE_PREFIX = "fred-"

#: The namespace identifiers are derived within. FRED carries the OECD's and
#: the IMF's figures at its own revision and rounding, so the same measure taken
#: from the agency that publishes it is a different series and must not land on
#: the same identifier.
NAMESPACE = "fred"

#: "Value of Exports to Indonesia from Utah". Indonesia is the partner in these
#: series, not the subject: the figures are Utah's, and ninety-five of them
#: would otherwise land in the warehouse as Indonesian statistics.
_PARTNER = re.compile(r"\bto\s+indonesia\s+from\s+", re.IGNORECASE)

#: An ISO date, which is the only shape FRED's first column takes.
_ISO_DATE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})$")

#: How a declared frequency restates a date as the period it belongs to.
#: Weekly and daily series are left as the date they carry: a week has no
#: canonical label, and inventing one would be a claim the source does not make.
QUARTER_OF_MONTH = {1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 2, 7: 3, 8: 3, 9: 3, 10: 4, 11: 4, 12: 4}


def indicator_id(series_id: str) -> str:
    """The Silver identifier for one FRED series.

    Keyed on the series id rather than the title, because the id is what FRED
    guarantees: the same title is published at three frequencies
    (`CCUSMA02IDA618N`, `...IDM618N`, `...IDQ618N`), and an identifier built
    from the title would have the three overwrite one another in Silver,
    silently, leaving whichever ran last.
    """
    return short_id(NAMESPACE, series_id)


def period_label(published: str, frequency: str | None) -> str:
    """Restate a published observation date at the series' own frequency.

    FRED dates every observation at the first day of the period it covers, so a
    quarterly series' `1952-04-01` is Q2 1952 and not the 1st of April. Left as
    the date, Silver would read seven hundred quarterly series as daily ones.

    An unrecognised frequency keeps the date, which is the honest fallback: a
    five-yearly series is not annual, and pretending otherwise would date a
    figure to a year it does not describe.
    """
    match = _ISO_DATE.match(published.strip())
    if match is None:
        return published.strip()

    year = match.group("year")
    month = int(match.group("month"))
    declared = (frequency or "").strip().lower()

    if declared.startswith("annual") or declared == "yearly":
        return year
    if declared.startswith("semiannual"):
        return f"{year}-S{1 if month <= 6 else 2}"
    if declared.startswith("quarterly"):
        return f"{year}-Q{QUARTER_OF_MONTH[month]}"
    if declared.startswith("monthly"):
        return f"{year}-{month:02d}"
    return published.strip()


def _country_of(title: str | None) -> str:
    """The place a series' figures are about, as far as its title says."""
    text = str(title or "")
    if not text or _PARTNER.search(text):
        return ""
    return "Indonesia" if "indonesia" in text.lower() else ""


class FredExtractor(Extractor):
    """One Bronze record per observation, with the listing's metadata on it."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug.startswith(SOURCE_PREFIX) and landed.path.suffix.lower() == ".csv"

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        text = decode(landed.path.read_bytes())
        if not text.strip():
            return

        rows = csv.reader(io.StringIO(text))
        header = next(rows, None)
        if header is None or len(header) < 2:
            # A one-column download is FRED answering with something that is
            # not a series. Nothing to extract, and nothing worth failing the
            # corpus over.
            return

        extra = landed.extra or {}
        # The header's second column is the series identifier, which is the
        # file's own statement of what it holds; the sidecar is the fallback
        # for a download whose header FRED has reshaped.
        series_id = header[1].strip() or str(extra.get("series_id") or "")
        title = extra.get("title")
        frequency = extra.get("frequency")

        common = {
            "series_id": series_id,
            "indicator": indicator_id(series_id),
            "title": str(title or ""),
            "units": str(extra.get("units") or ""),
            "frequency": str(frequency or ""),
            "seasonal_adjustment": str(extra.get("seasonal_adjustment") or ""),
            # Only the series whose title says they are Indonesia's. The
            # search ranks by relevance, so it returns both series about
            # Indonesia and series that merely trade with it — and a state's
            # exports *to* Indonesia are that state's figures. A title that
            # names no country at all (the rupiah-denominated issuance series)
            # leaves this empty rather than guessing, which keeps the gap
            # visible in Silver instead of asserting a place.
            "country": _country_of(title),
        }

        for number, row in enumerate(rows, start=1):
            if len(row) < 2 or not row[0].strip():
                continue
            published = row[0].strip()
            yield {
                "dataset": landed.dataset or "fred",
                "row_number": number,
                "columns": {
                    **common,
                    "date": published,
                    "period": period_label(published, str(frequency or "")),
                    "value": row[1].strip(),
                },
            }


#: What a series page states that the download cannot. FRED repeats the source
#: line once per contributing agency, so there can be several.
_PAGE_SOURCE = re.compile(r'<a[^>]*class="note-source[^"]*"[^>]*>(.*?)</a>', re.DOTALL)
_PAGE_RELEASE = re.compile(r'<a[^>]*class="note-release[^"]*"[^>]*>(.*?)</a>', re.DOTALL)
_PAGE_NOTES = re.compile(r'<p class="series-notes[^"]*">(.*?)</p>', re.DOTALL)
#: "Title (CODE) | FRED | St. Louis Fed". Read off the document title rather
#: than the heading, which wraps the title around a favourites button.
_PAGE_TITLE = re.compile(r"<title>\s*(.*?)\s*</title>", re.DOTALL)

_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"[^\S\n]+")


def _text(markup: str) -> str:
    """Tag soup to readable text, keeping the line breaks it declares."""
    return unescape(_SPACE.sub(" ", _TAG.sub(" ", markup))).strip()


def _notes(markup: str) -> str:
    """Notes markup as text, keeping the paragraphs FRED writes as `<br><br>`.

    Kept because the notes are what say what a series counts, and three
    paragraphs run together into a wall of prose nobody reads.
    """
    text = "\n".join(_text(part) for part in _BREAK.split(markup))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class FredSeriesPageExtractor(Extractor):
    """One Bronze record per series page: who publishes it, and what it counts.

    A record rather than a document, though the artifact is a page: what is
    wanted from it is four fields, not its prose, and the indicators table is
    built by reading them beside the figures they describe.
    """

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return (
            landed.source_slug.startswith(SOURCE_PREFIX)
            and landed.path.suffix.lower() in {".html", ".htm"}
            # The search listing is landed as HTML too, and it is a page of
            # sixty series rather than one series' description.
            and (landed.dataset or "").endswith("series-pages")
        )

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        html = decode(landed.path.read_bytes())
        extra = landed.extra or {}
        series_id = str(extra.get("series_id") or landed.path.stem)

        title = _PAGE_TITLE.search(html)
        heading = _text(title.group(1)).split(" | ")[0].strip() if title else ""
        if heading.endswith(f"({series_id})"):
            # The code, which the page prints after the name and which is a
            # column of its own here.
            heading = heading[: -len(f"({series_id})")].strip()

        sources = tuple(dict.fromkeys(_text(s) for s in _PAGE_SOURCE.findall(html) if _text(s)))
        release = next((_text(r) for r in _PAGE_RELEASE.findall(html) if _text(r)), None)
        notes = _PAGE_NOTES.search(html)

        yield {
            "dataset": landed.dataset or "fred-series-pages",
            "row_number": 1,
            "columns": {
                "series_id": series_id,
                "indicator": indicator_id(series_id),
                "title": heading or str(extra.get("title") or ""),
                # Every agency FRED credits, in the order the page lists them:
                # the figures behind one series are often assembled by three.
                "publisher": "; ".join(sources),
                "release": release or "",
                "notes": _notes(notes.group(1)) if notes else "",
            },
        }
