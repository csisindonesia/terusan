"""HEESI — the Handbook of Energy and Economic Statistics of Indonesia.

Collected as a document, and only as a document. The handbook is the reference
an Indonesian energy question is answered from — production, consumption,
generation, reserves, trade and prices, by fuel and by sector — and holding the
PDF is worth doing on its own: it is searchable, citable, and served back with
its licence beside it.

No figures are published from it. Reading its fourteen tables needs a parser
written for them one table at a time; that parser was begun, was not finished,
and has been taken out rather than left half-working. So this lands editions
and stops there, and nothing downstream claims to have read them.

ESDM publishes one PDF a year, and every edition back to 2010 is linked from
the same publication page. So the editions are read off that page rather than
taken from a tracker spreadsheet somebody maintains by hand: the page is what
ESDM publishes, and a tracker is a second copy of it that goes stale.

Editions are landed newest-first, one artifact each, and a run with no `--limit`
takes only the newest. The archive is fifteen editions of ten-megabyte PDFs,
and a full pull is a deliberate act rather than the default.

The edition year in the filename is the handbook's own — the 2025 edition
carries figures through 2025 — so it names the partition. A revision published
later under the same name lands as a new document, because landing is
content-addressed and the bytes differ.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
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

#: The English edition's page. ESDM publishes the same PDFs under an Indonesian
#: URL, and the handbook itself is bilingual, so either page reaches the same
#: files.
PUBLICATION_PAGE = (
    "https://www.esdm.go.id/en/publication/"
    "handbook-of-energy-economic-statistics-of-indonesia-heesi"
)

DATASET = "handbook"

#: Every handbook PDF on the page. The filenames run
#: `content-handbook-of-energy-and-economic-statistics-of-indonesia-2025.pdf`,
#: with older editions dropping the second `and` and some carrying a cache-
#: busting suffix, so the match is deliberately loose about everything except
#: the words that identify the publication.
_LINK = re.compile(r'href="([^"]*handbook-of-energy[^"]*\.pdf)"', re.IGNORECASE)

#: The four-digit edition year, taken from the end of the filename rather than
#: anywhere inside it: `...-of-indonesia-2018-final-edition.pdf` has the year
#: before a suffix, and a looser match would read `2010` out of a hash.
_EDITION = re.compile(r"-(?P<year>19|20\d{2})(?:-|\.pdf$|_)", re.IGNORECASE)

#: A handbook is ten megabytes and the site is not fast.
TIMEOUT_SECONDS = 180.0


@dataclass(frozen=True, slots=True)
class Edition:
    """One handbook edition, as the publication page links it."""

    year: int
    url: str

    @property
    def filename(self) -> str:
        name = urlparse(self.url).path.rsplit("/", 1)[-1]
        return name or f"heesi-{self.year}.pdf"


def editions(page: str, base: str = PUBLICATION_PAGE) -> list[Edition]:
    """Every edition linked from the page, newest first.

    One entry per year: ESDM links some editions twice — once in the body and
    once in a sidebar — and landing the same bytes twice is wasted bandwidth
    even though it deduplicates on arrival.
    """
    found: dict[int, Edition] = {}
    for href in _LINK.findall(page):
        url = str(urljoin(base, href))
        match = _EDITION.search(url.rsplit("/", 1)[-1])
        if match is None:
            # A file whose name states no year cannot be filed under an
            # edition, and guessing from the page order would put this year's
            # figures under last year's.
            log.warning("heesi.unnamed_edition", url=url)
            continue
        year = int(match.group("year"))
        found.setdefault(year, Edition(year=year, url=url))
    return sorted(found.values(), key=lambda e: e.year, reverse=True)


class Handbook(Source):
    """The handbook PDFs, newest first."""

    meta = SourceMeta(
        slug="esdm-heesi",
        name="HEESI — Handbook of Energy and Economic Statistics of Indonesia",
        organization="Kementerian Energi dan Sumber Daya Mineral",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.BULK_DOWNLOAD,
        base_url=PUBLICATION_PAGE,
        license="ESDM terms of use",
        update_frequency=UpdateFrequency.ANNUAL,
        max_requests_per_second=1.0,
        # The handbook lands late in the year and is revised afterwards, so
        # check monthly rather than guessing a release date. Landing is
        # content-addressed, so an unchanged month writes nothing.
        schedule="0 4 5 * *",
        notes=(
            "One PDF per edition, collected as a document. Nothing reads its "
            "tables: no figures are published from this source."
        ),
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        with fetcher(timeout=TIMEOUT_SECONDS) as http:
            page = http.get(PUBLICATION_PAGE)
            available = editions(page.text, str(page.url))
            if not available:
                raise ValueError(
                    f"no handbook PDFs linked from {PUBLICATION_PAGE}; the page "
                    "markup has changed and the links must be found again"
                )

            if ctx.since:
                # The edition year, not a publication date: ESDM states no date
                # per edition, and the year is what says whether an edition is
                # newer than the last run's.
                available = [e for e in available if e.year >= ctx.since.year]

            wanted = available[: ctx.limit] if ctx.limit else available[:1]

            for edition in wanted:
                response = http.get(edition.url)

                yield Artifact(
                    content=response.content,
                    filename=edition.filename,
                    dataset=DATASET,
                    source_url=edition.url,
                    media_type="application/pdf",
                    # The edition year as a date: the handbook covers the year
                    # it is named for and states no publication day.
                    published_at=date(edition.year, 12, 31),
                    partition=(f"edition={edition.year}",),
                    metadata={
                        # What the handbook is called, stated rather than left
                        # to be derived: the filename ESDM's CMS serves it
                        # under begins "content-", and a catalogue that reads
                        # its titles off filenames would list this as "Content
                        # Handbook of Energy...".
                        "title": (
                            "Handbook of Energy and Economic Statistics "
                            f"of Indonesia {edition.year}"
                        ),
                        "document_type": "publication",
                        "edition": str(edition.year),
                        "page_url": PUBLICATION_PAGE,
                        "last_modified": response.headers.get("last-modified"),
                        "etag": response.headers.get("etag"),
                    },
                )
