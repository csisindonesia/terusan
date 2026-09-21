"""SIPRI Military Expenditure Database — one workbook, published yearly.

SIPRI puts the whole database behind a single link on
https://www.sipri.org/databases/milex: an `.xlsx` holding every country from
1949, one sheet per measure. The link's filename carries the coverage and the
revision — `SIPRI-Milex-data-1949-2025_v1.2.xlsx` — and both change when SIPRI
republishes, so the URL is read off the page rather than hardcoded. A pinned
URL keeps working after a revision and quietly serves last year's figures.

The workbook is landed whole, Indonesia and all. Narrowing happens in
extraction, where the bytes are still on disk if the choice is ever revisited
— a source that dropped two hundred countries before landing would have to go
back to SIPRI to get them, and SIPRI takes the previous revision down
(program.md §2.1).
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

DATABASE_PAGE = "https://www.sipri.org/databases/milex"

#: The Bronze dataset the workbook lands under.
DATASET = "milex"

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: Every workbook on the page, in order. SIPRI writes the href without a
#: scheme — `//www.sipri.org/sites/default/files/...` — which `urljoin`
#: resolves against the page.
_LINK = re.compile(r'href="([^"]+\.xlsx)"', re.IGNORECASE)

#: `SIPRI-Milex-data-1949-2025_v1.2.xlsx`: the years it covers, and the
#: revision within that year. Both are stated nowhere else in the response.
_FILENAME = re.compile(
    r"milex-data-(?P<from>\d{4})[-–](?P<to>\d{4})(?:[_-]v(?P<version>\d+(?:\.\d+)*))?",
    re.IGNORECASE,
)


def workbook_url(page: str, base: str = DATABASE_PAGE) -> str:
    """The database download, as the page currently links it.

    Raises rather than guessing at a URL: an empty match means SIPRI reshaped
    the page, and a guessed link either 404s or lands a file nobody checked.
    """
    for href in _LINK.findall(page):
        resolved = str(urljoin(base, href))
        if "milex" in resolved.rsplit("/", 1)[-1].lower():
            return resolved
    raise ValueError(
        f"no Milex workbook linked from {base}; the page markup has changed "
        "and the link must be found again"
    )


def _last_modified(header: str | None) -> date | None:
    if not header:
        return None
    try:
        return parsedate_to_datetime(header).date()
    except (TypeError, ValueError):
        return None


class MilitaryExpenditure(Source):
    """The Milex workbook, landed as published."""

    meta = SourceMeta(
        slug="sipri-milex",
        name="SIPRI — Military Expenditure Database",
        organization="Stockholm International Peace Research Institute",
        category=Category.STATISTICS,
        source_type=SourceType.RESEARCH_REPOSITORY,
        collection_method=CollectionMethod.BULK_DOWNLOAD,
        base_url=DATABASE_PAGE,
        # The workbook is every country; the extractor keeps Indonesia. The
        # record describes what SIPRI publishes, not what we retain.
        country=None,
        license="SIPRI terms of use — free for non-commercial use with citation",
        update_frequency=UpdateFrequency.ANNUAL,
        max_requests_per_second=1.0,
        # SIPRI releases the database each April, usually revising it once or
        # twice afterwards. A monthly check finds the revision; landing is
        # content-addressed, so an unchanged month writes nothing.
        schedule="0 4 3 * *",
        notes="One workbook per release. Indonesia is selected at extraction.",
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        with fetcher() as http:
            page = http.get(DATABASE_PAGE)
            url = workbook_url(page.text, str(page.url))

            response = http.get(url)
            published = _last_modified(response.headers.get("last-modified"))

            # Fetched before the `since` check rather than after a HEAD: the
            # file is under a megabyte and published once a year, so a second
            # round trip to save one download a year is not worth the branch.
            if ctx.since and published and published < ctx.since:
                log.info("sipri.unchanged", published=str(published), since=str(ctx.since))
                return

        filename = urlparse(url).path.rsplit("/", 1)[-1] or "sipri-milex.xlsx"
        edition = _FILENAME.search(filename)

        yield Artifact(
            content=response.content,
            filename=filename,
            dataset=DATASET,
            source_url=url,
            media_type=XLSX_MEDIA_TYPE,
            published_at=published,
            # One workbook per release, so the coverage's last year names the
            # edition. It is what a reader asks for — "the 2025 figures" — and
            # what prunes a query to one release.
            partition=(f"edition={edition.group('to')}",) if edition else (),
            metadata={
                "page_url": DATABASE_PAGE,
                "version": edition.group("version") if edition else None,
                "covers_from": edition.group("from") if edition else None,
                "covers_to": edition.group("to") if edition else None,
                "last_modified": response.headers.get("last-modified"),
                "etag": response.headers.get("etag"),
            },
        )
