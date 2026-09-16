"""Bank Indonesia SDDS — real sector indicators.

The Special Data Dissemination Standard page: national accounts, production
index, labour market and prices, latest two observations. One HTML page, landed
whole.

Ported from an earlier warehouse, where it was generated rather than
hand-written — which is why it does so little. Extraction of the page's tables
belongs in `terusan_pipelines.extract`, not here (program.md §2.1).
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
from ..http import fetcher

BASE_URL = "https://www.bi.go.id/id/statistik/sdds/Default.aspx"

TIMEOUT_SECONDS = 120.0


class SddsRealSector(Source):
    meta = SourceMeta(
        slug="bi-sdds-real-sector",
        name="Bank Indonesia — SDDS Real Sector",
        organization="Bank Indonesia",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url=BASE_URL,
        license="Bank Indonesia terms of use",
        update_frequency=UpdateFrequency.MONTHLY,
        max_requests_per_second=2.0,
        schedule="0 5 * * 1",
        notes="Ported from the lake warehouse.",
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        with fetcher(timeout=TIMEOUT_SECONDS) as http:
            response = http.get(BASE_URL)
            yield Artifact(
                content=response.content,
                filename="sdds-real-sector.html",
                dataset="real-sector",
                source_url=str(response.url),
                media_type="text/html",
                metadata={"etag": response.headers.get("etag")},
            )
