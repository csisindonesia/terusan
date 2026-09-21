"""FRED — every Indonesian series its search returns.

FRED holds the Indonesian figures that the OECD, the IMF, the World Bank and
the BIS republish, each as a series with a full history behind it. The search
page is the only index of them that does not need an API key:
`/searchresults?st=indonesia&pageID=1` through `pageID=12`, sixty results a
page.

Two things are landed, and both are needed:

- the search pages themselves, which are the only record of what FRED offered
  under "indonesia" on a given day — a series that is discontinued and dropped
  from the results leaves no other trace;
- one CSV per series, from `/graph/fredgraph.csv?id=<series>`, which is the
  whole history rather than the latest reading. That is what makes this source
  different from a page scraper: a single fetch carries sixty years of monthly
  figures, so history does not have to accrue one daily snapshot at a time;
- the series page, `/series/<series>`, which is the only place FRED states who
  publishes the figures, which release they arrive in, and the notes explaining
  what the series counts. None of that is in the CSV and none of it is in the
  search listing, and a figure whose definition is missing is a figure nobody
  can use safely.

The series' descriptive metadata — its title, units, frequency, seasonal
adjustment and coverage — is read off the listing and carried in the artifact's
sidecar, because the CSV itself states none of it: its columns are
`observation_date` and the series id, and nothing says whether the numbers are
rupiah or an index.

No API key. FRED's own API would give the same figures as JSON, but it needs a
registered key, and a warehouse that cannot be re-run by whoever inherits it
without first obtaining credentials is worse than one that reads the CSV
endpoint the site serves to any browser.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from html import unescape

import structlog

from ..base import (
    Artifact,
    Category,
    CollectionMethod,
    ScrapeContext,
    Source,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..http import fetcher
from ..ratelimit import HostRateLimiter

log = structlog.get_logger(__name__)

ROOT = "https://fred.stlouisfed.org"

#: What the search is asked for. One term, not a list: the search ranks by
#: relevance to it, so two terms would be two crawls with two result sets, and
#: this one is what the portal is about.
SEARCH_TERM = "indonesia"

#: How deep the crawl goes. Sixty results a page, so twelve pages is the seven
#: hundred series the search ranks highest — beyond that the results are
#: material that merely mentions Indonesia in a footnote.
SEARCH_PAGES = 12

#: The listing as a collection of its own, so a reader can see which series
#: FRED offered on a given day. It produces no observations, so it does not
#: appear in the portal's catalogue beside the figures.
SEARCH_DATASET = "fred-indonesia-search"

#: The series, as one collection. This name reaches the portal: Silver
#: observations carry the Bronze dataset they came from, and the catalogue
#: lists collections by it.
SERIES_DATASET = "fred-indonesia-series"

#: The series pages, landed beside the figures rather than folded into them.
#: They are what the indicators table is described from — publisher, release
#: and notes — and they are a page each, so keeping them apart means the
#: figures do not carry a paragraph of prose on every row.
SERIES_PAGE_DATASET = "fred-indonesia-series-pages"

#: One result block, as the page writes it. Anchored on the list item class
#: rather than on the layout inside it: FRED rearranges the meta line often
#: enough, and an unmatched inner field is a missing attribute rather than a
#: missing series.
_ITEM = re.compile(r'<li class="search-list-item">(.*?)</li>', re.DOTALL)
_TITLE = re.compile(
    r'<a href="/series/([A-Za-z0-9_]+)"[^>]*class="series-title[^"]*"[^>]*>\s*(.*?)\s*</a>',
    re.DOTALL,
)
_META = re.compile(r'<span class="search-result-meta">\s*(.*?)\s*</span>', re.DOTALL)
_DATES = re.compile(r'<span class="search-result-meta-dates[^"]*">\s*(.*?)\s*</span>', re.DOTALL)
_NOTES = re.compile(r'<div class="search-series-notes[^"]*">\s*<p>\s*(.*?)\s*</p>', re.DOTALL)

#: "Q2 1952 to Q1 2026 (May 7)" — the coverage the listing prints, and when the
#: series was last updated. The update is kept as written: FRED prints it as a
#: month and a day, an ISO date or "13 hours ago" depending on how recent it
#: is, and inventing the missing year would be a guess.
_SPAN = re.compile(r"^(?P<start>.+?)\s+to\s+(?P<end>.+?)(?:\s*\((?P<updated>.*)\))?$")

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")

#: A guard against a search page that changes shape. Sixty results a page is
#: what FRED serves; a parser suddenly finding thousands means the markup moved
#: and the run should stop rather than crawl the site.
MAX_RESULTS_PER_PAGE = 200


def search_url(page: int) -> str:
    return f"{ROOT}/searchresults?st={SEARCH_TERM}&pageID={page}"


def series_url(series_id: str) -> str:
    """Where the series is published, which is what a citation points at."""
    return f"{ROOT}/series/{series_id}"


def csv_url(series_id: str) -> str:
    """The full history, as served to a browser's download button."""
    return f"{ROOT}/graph/fredgraph.csv?id={series_id}"


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One series as the listing describes it.

    Everything here comes off the search page rather than the CSV, which states
    none of it. It travels in the artifact's sidecar so that extraction can put
    a unit and a frequency beside each figure without fetching the series page
    again.
    """

    series_id: str
    title: str
    units: str | None = None
    frequency: str | None = None
    seasonal_adjustment: str | None = None
    observation_start: str | None = None
    observation_end: str | None = None
    #: As printed: "May 7", "2025-05-15", "13 hours ago".
    updated: str | None = None
    notes: str | None = None
    #: Which search page carried it. Results drift between requests, so a
    #: series can appear on two pages of one crawl; this records where it was
    #: first seen rather than asserting a rank.
    page: int | None = None


def _text(markup: str) -> str:
    """Tag soup to a single line of readable text."""
    return unescape(_SPACE.sub(" ", _TAG.sub(" ", markup))).strip()


def _split_meta(meta: str) -> tuple[str | None, str | None, str | None]:
    """Split "Rupiah, Monthly, Not Seasonally Adjusted" into its three parts.

    Taken from the right, two commas in. The units are the part that varies and
    the part that carries commas of its own — "Births per 1,000 Women Ages
    15-19", "US Dollars, Sum Over Component Sub-periods" — while the frequency
    and the seasonal adjustment are single words from a short list. Splitting on
    every comma instead would cut those units in half, and rejoining them would
    not put the spacing back where the publisher had it.
    """
    text = meta.strip()
    if not text:
        return None, None, None

    head, comma, seasonal = text.rpartition(",")
    if not comma:
        return text, None, None

    units, comma, frequency = head.rpartition(",")
    if not comma:
        # Two parts only: a series whose seasonal adjustment is not stated.
        return head.strip() or None, seasonal.strip() or None, None

    return units.strip() or None, frequency.strip() or None, seasonal.strip() or None


def _split_span(span: str) -> tuple[str | None, str | None, str | None]:
    """Split "Jan 1967 to Jul 2026 (Aug 17)" into start, end and update."""
    match = _SPAN.match(span)
    if not match:
        return None, None, None
    return (
        match.group("start") or None,
        match.group("end") or None,
        (match.group("updated") or "").strip() or None,
    )


def search_results(html: str, page: int | None = None) -> list[SearchResult]:
    """Every series a search page lists, in the order it lists them.

    Page order is kept rather than sorted: FRED ranks by relevance, and the
    rank is the one thing the listing says that the series page does not.
    """
    results: list[SearchResult] = []

    for block in _ITEM.findall(html):
        title = _TITLE.search(block)
        if title is None:
            # A result block with no series link is a group header or an
            # advertisement for a release, not a series.
            continue

        meta = _META.search(block)
        units, frequency, seasonal = _split_meta(_text(meta.group(1)) if meta else "")

        dates = _DATES.search(block)
        start, end, updated = _split_span(_text(dates.group(1)) if dates else "")

        notes = _NOTES.search(block)

        results.append(
            SearchResult(
                series_id=title.group(1),
                title=_text(title.group(2)),
                units=units,
                frequency=frequency,
                seasonal_adjustment=seasonal,
                observation_start=start,
                observation_end=end,
                updated=updated,
                notes=_text(notes.group(1)) if notes else None,
                page=page,
            )
        )

    return results


class FredIndonesia(Source):
    """FRED's Indonesian series: the listing, then each series' full history."""

    meta = SourceMeta(
        slug="fred-indonesia",
        name="FRED — Indonesian economic series",
        organization="Federal Reserve Bank of St. Louis",
        category=Category.STATISTICS,
        # FRED redistributes the OECD's, the IMF's, the World Bank's and the
        # BIS's figures under its own identifiers and revisions rather than
        # publishing on their behalf, which is the same position Trading
        # Economics is in.
        source_type=SourceType.MARKET_DATA,
        collection_method=CollectionMethod.SCRAPE,
        base_url=search_url(1),
        country="ID",
        license=(
            "FRED terms of use — most series are redistributed from the OECD, "
            "IMF, World Bank and BIS under their own terms; check the series "
            "page before republishing a figure"
        ),
        update_frequency=UpdateFrequency.WEEKLY,
        # Monday 04:00. The series here are monthly, quarterly and annual
        # releases from international organizations, which arrive on their own
        # calendars — a daily crawl of seven hundred CSVs would land the same
        # bytes six days out of seven. The daily series among them are FX and
        # rates, and a week's worth arrives in one CSV anyway.
        schedule="0 4 * * 1",
        # Two requests a second: seven hundred series is six minutes, which is
        # slow enough not to look like a crawl. robots.txt allows /series/ and
        # /graph/, and disallows only the search-with-filters URLs the crawl
        # does not use.
        max_requests_per_second=2.0,
        notes=(
            f"Walks {SEARCH_PAGES} pages of the `{SEARCH_TERM}` search and "
            "lands each series' page and its whole history as CSV. The title, "
            "units, frequency, publisher, release and notes ride in the "
            "sidecar: the CSV states none of them."
        ),
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        today = date.today()
        # Monthly partitions, as elsewhere: seven hundred directories a week is
        # thirty-six thousand a year, and a year in one directory is slow to
        # list on a NAS long before it is slow to read.
        partition = (f"year={today.year}", f"month={today.month:02d}")

        # The declared ceiling, enforced. A scraper holds no reference to the
        # runner's limiter, so it keeps its own for the one host it touches.
        limiter = HostRateLimiter(default_rate=self.meta.max_requests_per_second)

        # First seen wins. The search re-ranks between requests, so a series
        # can appear on two pages of one crawl; fetching it twice would land
        # the same bytes under two sidecars claiming different ranks.
        seen: dict[str, SearchResult] = {}

        # Artifacts yielded so far. `ctx.limit` counts them, as it does for
        # every other source and as the runner enforces it — so a smoke run
        # has to allow for the listing's twelve pages before it reaches a
        # series.
        landed = 0

        with fetcher(limiter=limiter) as http:
            for page in range(1, SEARCH_PAGES + 1):
                if ctx.limit is not None and landed >= ctx.limit:
                    break
                response = http.get(search_url(page))
                results = search_results(response.text, page)

                if not results:
                    # An empty page beyond the first is the end of the results;
                    # an empty first page means the markup moved.
                    if page == 1:
                        raise ValueError(
                            f"no series found on {search_url(1)}; the search page's "
                            "markup has changed and the parser needs updating"
                        )
                    log.info("fred.search_exhausted", page=page)
                    break

                if len(results) > MAX_RESULTS_PER_PAGE:
                    raise ValueError(
                        f"{len(results)} results on one search page, expected at most "
                        f"{MAX_RESULTS_PER_PAGE}; the page's markup has changed"
                    )

                landed += 1
                yield Artifact(
                    content=response.text.encode(),
                    filename=f"search-page-{page:02d}.html",
                    dataset=SEARCH_DATASET,
                    source_url=str(response.url),
                    media_type="text/html",
                    partition=partition,
                    metadata={
                        "search_term": SEARCH_TERM,
                        "page": page,
                        "series_count": len(results),
                        "series_ids": [result.series_id for result in results],
                    },
                )

                for result in results:
                    seen.setdefault(result.series_id, result)

            log.info("fred.listing", series=len(seen), pages=SEARCH_PAGES)

            # Sorted by identifier so two runs walk the series in the same
            # order, which is what makes a `--limit` smoke run comparable.
            for series_id in sorted(seen):
                if ctx.limit is not None and landed >= ctx.limit:
                    return

                result = seen[series_id]

                # The series page first: it carries the publisher, the release
                # and the notes, and it is landed whether or not the download
                # that follows works — a series withdrawn between the two is
                # still described by the page we have.
                page = http.try_get(series_url(series_id))
                if page is not None:
                    landed += 1
                    yield Artifact(
                        content=page.text.encode(),
                        filename=f"{series_id}.html",
                        dataset=SERIES_PAGE_DATASET,
                        source_url=series_url(series_id),
                        media_type="text/html",
                        partition=partition,
                        metadata={"series_id": series_id, "title": result.title},
                    )

                if ctx.limit is not None and landed >= ctx.limit:
                    return

                # try_get rather than get: one withdrawn series must not
                # discard the six hundred that answered.
                response = http.try_get(csv_url(series_id))
                if response is None:
                    log.warning("fred.series_failed", series=series_id)
                    continue

                landed += 1
                yield Artifact(
                    content=response.content,
                    filename=f"{series_id}.csv",
                    dataset=SERIES_DATASET,
                    # The series page, not the CSV endpoint: this is where the
                    # figures are published, and what a citation points at.
                    source_url=series_url(series_id),
                    media_type="text/csv",
                    partition=partition,
                    metadata={
                        "series_id": series_id,
                        "title": result.title,
                        "units": result.units,
                        "frequency": result.frequency,
                        "seasonal_adjustment": result.seasonal_adjustment,
                        "observation_start": result.observation_start,
                        "observation_end": result.observation_end,
                        "updated": result.updated,
                        # The listing's two-line version. The page carries the
                        # whole thing, and is landed beside this.
                        "notes": result.notes,
                        "search_page": result.page,
                        "download_url": csv_url(series_id),
                    },
                )
