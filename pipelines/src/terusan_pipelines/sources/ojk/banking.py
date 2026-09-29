"""OJK — Statistik Perbankan Indonesia.

The monthly banking statistics workbook, from the page OJK published it on
until June 2025. Editions after that are on the SJK portal and arrive through
`ojk-banking-sjk`; this source holds the archive before them.

The page lists only its ten newest editions. The older ones are still served,
each at `.../Pages/Statistik-Perbankan-Indonesia---<Bulan>-<Tahun>.aspx`, but
nothing links to them any more — so a full pull walks back month by month from
the oldest listed edition, asking for each page by its name, and stops once a
run of months answers nothing. The walk is only taken on `--param full=true`:
the archive is frozen, so a routine run has nothing to find there.

Editions up to late 2024 publish the workbook inside a `.zip` beside a PDF
rather than as a bare `.xlsx`. Both land as served; the archive reader opens
the zip.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterator
from pathlib import Path

import requests
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
from ..portals import wants
from .legacy import banking_spi

log = structlog.get_logger(__name__)

#: How the edition pages are named, month by month.
MONTH_NAMES = {number: name.capitalize() for name, number in banking_spi.MONTH_ID_TO_NUM.items()}

EDITION_PAGE = (
    f"{banking_spi.BASE}/id/kanal/perbankan/data-dan-statistik/statistik-perbankan-indonesia"
    "/Pages/Statistik-Perbankan-Indonesia---{month}-{year}.aspx"
)

#: A walk back stops after this many consecutive months with no page. One or
#: two missing is an edition published under a different name; a year of them
#: is the start of the archive.
MISSES_END_ARCHIVE = 12

#: The files an edition page may carry the workbook as, in preference order.
_WORKBOOK = re.compile(r"\.xlsx$", re.IGNORECASE)
_ARCHIVE = re.compile(r"\.zip$", re.IGNORECASE)

MEDIA_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "zip": "application/zip",
}


class BankingStatistics(Source):
    meta = SourceMeta(
        slug="ojk-banking-spi",
        name="OJK — Statistik Perbankan Indonesia",
        organization="Otoritas Jasa Keuangan",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.BULK_DOWNLOAD,
        base_url=banking_spi.BASE,
        license="OJK terms of use",
        update_frequency=UpdateFrequency.MONTHLY,
        max_requests_per_second=1.0,
        schedule="0 3 15-25 * *",
        notes=(
            "Vendored scraper. One workbook per monthly edition, up to June "
            "2025; ojk-banking-sjk continues from July 2025. The page lists ten "
            "editions; `--param full=true` walks back through the unlisted "
            "ones, which older editions publish as a zip."
        ),
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        listed = banking_spi.list_editions()
        if not listed:
            raise ValueError("no SPI editions found on the OJK portal")

        landed = 0
        missed: list[str] = []
        for edition in self._editions(ctx, listed):
            if ctx.limit is not None and landed >= ctx.limit:
                return
            url = _workbook_url(edition)
            if url is None:
                log.warning("ojk_spi.no_workbook", edition=edition.label, page=edition.page_url)
                continue
            extension = url.rsplit(".", 1)[-1].lower()

            # The vendored downloader writes to a temporary file and hands back
            # the path; the bytes are what gets landed.
            try:
                path = Path(banking_spi.download_xlsx(url))
            except requests.HTTPError as exc:
                # One edition refusing is no reason to leave the other hundred
                # of a backfill unfetched; the run still fails, below, and says
                # which ones are missing.
                log.warning("ojk_spi.download_failed", edition=edition.label, error=str(exc)[:200])
                missed.append(edition.label)
                continue
            try:
                yield Artifact(
                    content=path.read_bytes(),
                    filename=f"spi-{_edition_slug(edition)}.{extension}",
                    dataset="banking-statistics",
                    source_url=url,
                    media_type=MEDIA_TYPES[extension],
                    metadata={"edition": _edition_slug(edition), "page_url": edition.page_url},
                )
            finally:
                path.unlink(missing_ok=True)
            landed += 1

        if missed:
            raise RuntimeError(f"{len(missed)} SPI editions could not be downloaded: {missed}")

    def _editions(
        self, ctx: ScrapeContext, listed: list[banking_spi.SpiEdition]
    ) -> Iterator[banking_spi.SpiEdition]:
        """The editions this run takes, newest first.

        Without `full`, the newest listed one unless a limit asks for more.
        """
        if not wants(ctx, "full"):
            yield from listed[: ctx.limit] if ctx.limit else listed[:1]
            return

        yield from listed
        oldest = min(listed, key=lambda e: (e.year, e.month))
        year, month, misses = oldest.year, oldest.month, 0
        while misses < MISSES_END_ARCHIVE:
            year, month = (year - 1, 12) if month == 1 else (year, month - 1)
            edition = unlisted_edition(year, month)
            if _page_exists(edition.page_url):
                misses = 0
                yield edition
            else:
                misses += 1


def unlisted_edition(year: int, month: int) -> banking_spi.SpiEdition:
    """An edition the page no longer links, named the way the listed ones are."""
    name = MONTH_NAMES[month]
    return banking_spi.SpiEdition(
        label=f"Statistik Perbankan Indonesia - {name} {year}",
        year=year,
        month=month,
        page_url=EDITION_PAGE.format(month=name, year=year),
    )


def _page_exists(url: str) -> bool:
    try:
        banking_spi._get(url)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return False
        raise
    return True


def _workbook_url(edition: banking_spi.SpiEdition) -> str | None:
    """The edition's workbook, bare where it is published bare, else its zip."""
    page = banking_spi._get(edition.page_url).text
    # Some pages write the link as an absolute URL with its colon escaped —
    # `https&#58;//www.ojk.go.id/...` — which joined as written becomes a path
    # under ojk.go.id and a 404.
    hrefs = [html.unescape(href) for href, _ in banking_spi._extract_anchors(page)]
    for pattern in (_WORKBOOK, _ARCHIVE):
        for href in hrefs:
            if pattern.search(href.split("?", 1)[0]):
                return requests.compat.urljoin(banking_spi.BASE, href)
    return None


def _edition_slug(edition: object) -> str:
    """A filename-safe name for an edition, however the module labels it."""
    for attribute in ("slug", "label", "name", "title", "period"):
        value = getattr(edition, attribute, None)
        if value:
            return str(value)
    return str(edition)
