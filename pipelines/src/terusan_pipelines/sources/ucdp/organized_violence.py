"""UCDP's country-year organized violence dataset, published once a year.

This is what https://ucdp.uu.se/country/850 charts for Indonesia: every death
UCDP attributes to organized violence inside the country's borders, by year and
by type — state-based conflict, non-state conflict, and one-sided violence
against civilians — each with the low and high estimates that bound it.

Two things decide the shape of this source.

**The country page is a view, not a file.** UCDP publishes the figures behind
it as one global download, so the download is what lands and Indonesia is
selected at extraction. Scraping the country page instead would land a
rendering of numbers whose definitions, bounds and version live elsewhere.

**The API now wants a token; the downloads do not.** `ucdpapi.pcr.uu.se`
answers an unauthenticated caller with `API token required`, and a source that
needs a credential to collect anything is a source that collects nothing the
day the credential lapses. The same release is served as a public zip from the
downloads page, which is what this fetches.

The release is found on the page rather than hardcoded: the filename carries
the version — `organizedviolencecy-261-csv.zip` is 26.1 — and both the version
and the years it covers change every June. A pinned URL keeps working across a
release and quietly serves last year's figures.

UCDP's event-level GED is the other half of what the country page shows, and it
is not collected here: it is a 39 MB global archive and nothing downstream reads
events yet. It belongs in its own source beside this one, on the day something
does.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

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

log = structlog.get_logger(__name__)

DOWNLOADS_PAGE = "https://ucdp.uu.se/downloads/"

#: What the figures are about, for anyone reading the provenance: the same
#: release, as UCDP renders it for Indonesia.
COUNTRY_PAGE = "https://ucdp.uu.se/country/850"

#: The Bronze dataset the archive lands under.
DATASET = "organized-violence"

ZIP_MEDIA_TYPE = "application/zip"

#: The CSV archive of the country-year dataset, with the version in its name.
#: The page links the same release as CSV, Excel, Stata and R; CSV is taken
#: because it is the one format that needs no library to read back.
_CSV_LINK = re.compile(
    r'href="([^"]*organizedviolencecy-(\d+)-csv\.zip)"',
    re.IGNORECASE,
)


def _edition(digits: str) -> str:
    """`261` as UCDP writes it: 26.1, the year of the release and its revision.

    Returned unchanged where the shape is not the two-digit year plus a
    revision UCDP has used since 2017. Inventing a dot in the wrong place would
    name a release something UCDP never published.
    """
    return f"{digits[:2]}.{digits[2:]}" if len(digits) >= 3 else digits


def archive_url(page: str, base: str = DOWNLOADS_PAGE) -> tuple[str, str]:
    """The current release's CSV archive, and the version it carries.

    Raises rather than falling back to a guessed URL: an empty match means the
    downloads page was rebuilt, and a guess either 404s or lands a release
    nobody has looked at under a version read off the filename we guessed.
    """
    match = _CSV_LINK.search(page)
    if match is None:
        raise ValueError(
            f"no organized violence CSV archive linked from {base}; the page "
            "markup has changed and the link must be found again"
        )
    return str(urljoin(base, match.group(1))), _edition(match.group(2))


def _last_modified(header: str | None) -> date | None:
    if not header:
        return None
    try:
        return parsedate_to_datetime(header).date()
    except (TypeError, ValueError):
        return None


class OrganizedViolence(Source):
    """The country-year archive, landed as published."""

    meta = SourceMeta(
        slug="ucdp-organized-violence",
        name="UCDP — Organized violence, country-year",
        organization="Uppsala Conflict Data Program",
        category=Category.STATISTICS,
        source_type=SourceType.RESEARCH_REPOSITORY,
        collection_method=CollectionMethod.BULK_DOWNLOAD,
        base_url=DOWNLOADS_PAGE,
        # The archive is every country; the extractor keeps Indonesia. The
        # registry record describes what UCDP publishes, not what we retain.
        country=None,
        license="CC-BY-4.0",
        update_frequency=UpdateFrequency.ANNUAL,
        max_requests_per_second=1.0,
        # UCDP releases in June and revises afterwards. A monthly check finds
        # the revision; landing is content-addressed, so an unchanged month
        # writes nothing.
        schedule="0 4 5 * *",
        notes=(
            "One archive per release, aggregated from UCDP GED. Indonesia is "
            "selected at extraction; see ucdp.uu.se/country/850."
        ),
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        with fetcher() as http:
            page = http.get(DOWNLOADS_PAGE)
            url, edition = archive_url(page.text, str(page.url))

            response = http.get(url)
            published = _last_modified(response.headers.get("last-modified"))

            # Fetched before the `since` check rather than after a HEAD: the
            # archive is under two hundred kilobytes and published once a year,
            # so a second round trip to save one download a year buys nothing.
            if ctx.since and published and published < ctx.since:
                log.info(
                    "ucdp.unchanged",
                    published=str(published),
                    since=str(ctx.since),
                    edition=edition,
                )
                return

        filename = urlparse(url).path.rsplit("/", 1)[-1] or "organizedviolencecy-csv.zip"

        yield Artifact(
            content=response.content,
            filename=filename,
            dataset=DATASET,
            source_url=url,
            media_type=ZIP_MEDIA_TYPE,
            published_at=published,
            # One archive per release, so the version names the edition. It is
            # what a citation states — "UCDP 26.1" — and what prunes a query to
            # one release rather than to every year UCDP has ever published.
            partition=(f"edition={edition}",),
            metadata={
                "title": (
                    "UCDP Country-Year Dataset on Organized Violence "
                    f"within Country Borders {edition}"
                ),
                "document_type": "data_file",
                "edition": edition,
                "country_page": COUNTRY_PAGE,
                "etag": response.headers.get("etag"),
                "last_modified": response.headers.get("last-modified"),
            },
        )
