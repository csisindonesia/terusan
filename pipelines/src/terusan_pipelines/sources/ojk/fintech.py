"""OJK — peer-to-peer lending statistics.

Published as a workbook linked from a news article, so finding it is two hops:
the latest article, then the file on it. Both live in the vendored module.
"""

from __future__ import annotations

from collections.abc import Iterator

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
from .legacy import fintech_p2p


class PeerToPeerLending(Source):
    meta = SourceMeta(
        slug="ojk-fintech-p2p",
        name="OJK — Statistik Fintech Lending",
        organization="Otoritas Jasa Keuangan",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url=fintech_p2p.BASE,
        license="OJK terms of use",
        update_frequency=UpdateFrequency.MONTHLY,
        max_requests_per_second=1.0,
        schedule="0 3 20-28 * *",
        notes="Vendored scraper. The workbook is linked from the latest article.",
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        article_url = fintech_p2p.find_latest_article_url()
        workbook_url = fintech_p2p.find_workbook_url(article_url)

        yield Artifact(
            content=fintech_p2p.download_workbook(workbook_url),
            filename="fintech-lending.xlsx",
            dataset="p2p-lending",
            source_url=workbook_url,
            media_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            # The article is how the workbook was found, and the only record of
            # which release this file belongs to.
            metadata={"article_url": article_url},
        )
