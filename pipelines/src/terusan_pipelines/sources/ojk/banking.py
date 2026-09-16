"""OJK — Statistik Perbankan Indonesia.

The monthly banking statistics workbook. OJK has moved this between two
portals; the vendored modules cover both, and this Source uses the older one
because it still publishes a single workbook, which is a thing that can be
landed. The newer portal serves a grid API that returns parsed rows rather than
a file, so landing it faithfully means landing its responses — left for when a
reason to prefer it appears.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

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
from .legacy import banking_spi


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
        notes="Vendored scraper. One workbook per monthly edition.",
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        editions = banking_spi.list_editions()
        if not editions:
            raise ValueError("no SPI editions found on the OJK portal")

        # Newest first, and only as many as asked for: the archive runs to
        # years of monthly workbooks, and a full pull is a deliberate act.
        wanted = editions[: ctx.limit] if ctx.limit else editions[:1]

        for edition in wanted:
            url = banking_spi.get_xlsx_url(edition)
            if not url:
                continue

            # The vendored downloader writes to a temporary file and hands back
            # the path; the bytes are what gets landed.
            path = Path(banking_spi.download_xlsx(url))
            try:
                yield Artifact(
                    content=path.read_bytes(),
                    filename=f"spi-{_edition_slug(edition)}.xlsx",
                    dataset="banking-statistics",
                    source_url=url,
                    media_type=(
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    ),
                    metadata={"edition": _edition_slug(edition)},
                )
            finally:
                path.unlink(missing_ok=True)


def _edition_slug(edition: object) -> str:
    """A filename-safe name for an edition, however the module labels it."""
    for attribute in ("slug", "label", "name", "title", "period"):
        value = getattr(edition, attribute, None)
        if value:
            return str(value)
    return str(edition)
