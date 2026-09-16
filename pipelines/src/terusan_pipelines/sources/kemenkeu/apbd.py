"""DJPK — regional government budget realization (APBD).

The portal answers one month of one fiscal year per request, so a full pull is
a few hundred small responses rather than one file. Each is landed as served;
the vendored module knows the endpoint and the two different account-naming
schemes the portal has used since 2011.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

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
from .legacy import djpk_apbd

#: The portal's own year range starts here.
FIRST_YEAR = 2011


class RegionalBudgets(Source):
    """National-aggregate APBD realization, month by month."""

    meta = SourceMeta(
        slug="djpk-apbd",
        name="DJPK — Realisasi APBD",
        organization="Kementerian Keuangan (DJPK)",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.BULK_DOWNLOAD,
        base_url=djpk_apbd.PORTAL_PAGE_URL,
        license="Kementerian Keuangan open data",
        update_frequency=UpdateFrequency.MONTHLY,
        # A government server answering several hundred requests: slower than
        # the default, and the runner limits per host on top.
        max_requests_per_second=1.0,
        schedule="0 3 10 * *",
        notes="Vendored scraper. One request per fiscal month; figures are cumulative.",
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        today = date.today()
        # Only what is new, when the caller says so. Landing is
        # content-addressed, so re-fetching an unchanged month writes nothing —
        # but it still costs the portal a request.
        first_year = ctx.since.year if ctx.since else FIRST_YEAR

        emitted = 0
        for year in range(first_year, today.year + 1):
            last_month = today.month if year == today.year else 12
            for month in range(1, last_month + 1):
                if ctx.since and date(year, month, 1) < ctx.since.replace(day=1):
                    continue

                content = djpk_apbd.fetch_raw(periode=month, tahun=year)
                yield Artifact(
                    content=content,
                    filename=f"apbd-{year}-{month:02d}.xml",
                    dataset="apbd-national",
                    source_url=djpk_apbd.BASE_URL,
                    # SpreadsheetML: an XML dialect, not a real workbook.
                    media_type="application/xml",
                    partition=(f"year={year}",),
                    metadata={"periode": month, "tahun": year, "scope": "national"},
                )

                emitted += 1
                if ctx.limit is not None and emitted >= ctx.limit:
                    return
