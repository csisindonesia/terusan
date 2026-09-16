"""Bank Indonesia's monthly surveys, and its food price portal.

Each lands the file the agency publishes, exactly as served — the survey ZIPs,
the price portal's JSON — and leaves the reading of it to extraction. The
vendored modules beside these know how to reach each source and how to parse
what comes back; only the first half is used here (program.md §2.1).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, timedelta

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
from .legacy import consumer_survey, pihps_prices, retail_sales


class ConsumerSurvey(Source):
    """Survei Konsumen — the monthly consumer confidence release."""

    meta = SourceMeta(
        slug="bi-consumer-survey",
        name="Bank Indonesia — Survei Konsumen",
        organization="Bank Indonesia",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.BULK_DOWNLOAD,
        base_url=consumer_survey.ZIP_URL,
        license="Bank Indonesia terms of use",
        update_frequency=UpdateFrequency.MONTHLY,
        max_requests_per_second=1.0,
        # Published in the first half of the month for the month before.
        schedule="0 4 5-15 * *",
        notes="Vendored scraper. The ZIP holds one workbook of nine tables.",
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        yield Artifact(
            content=consumer_survey.download_zip(),
            filename="survei-konsumen.zip",
            dataset="consumer-survey",
            source_url=consumer_survey.ZIP_URL,
            media_type="application/zip",
        )


class RetailSalesSurvey(Source):
    """Survei Penjualan Eceran — the monthly retail sales release."""

    meta = SourceMeta(
        slug="bi-retail-sales-survey",
        name="Bank Indonesia — Survei Penjualan Eceran",
        organization="Bank Indonesia",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.BULK_DOWNLOAD,
        base_url=retail_sales.ZIP_URL,
        license="Bank Indonesia terms of use",
        update_frequency=UpdateFrequency.MONTHLY,
        max_requests_per_second=1.0,
        schedule="0 4 5-15 * *",
        notes="Vendored scraper. The ZIP holds one workbook of nine tables.",
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        yield Artifact(
            content=retail_sales.download_zip(),
            filename="survei-penjualan-eceran.zip",
            dataset="retail-sales",
            source_url=retail_sales.ZIP_URL,
            media_type="application/zip",
        )


class FoodPrices(Source):
    """PIHPS — the national food price information system.

    Queried by date range rather than downloaded whole, so a run lands the
    window it asked for. `ctx.since` narrows it; without one, the last week.
    """

    #: A week when nothing says otherwise. The portal publishes daily, and a
    #: longer default would re-fetch what earlier runs already landed.
    DEFAULT_DAYS = 7

    meta = SourceMeta(
        slug="bi-pihps-food-prices",
        name="PIHPS — Pusat Informasi Harga Pangan Strategis",
        organization="Bank Indonesia",
        category=Category.STATISTICS,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
        base_url=pihps_prices.BASE,
        license="Bank Indonesia terms of use",
        update_frequency=UpdateFrequency.DAILY,
        max_requests_per_second=2.0,
        schedule="0 7 * * *",
        notes="Vendored scraper. Daily strategic food prices by region.",
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        end = date.today()
        start = ctx.since or end - timedelta(days=self.DEFAULT_DAYS)

        rows = pihps_prices.fetch(start.isoformat(), end.isoformat())
        yield Artifact(
            # Re-serialized compactly: the API does not promise stable
            # whitespace, and unstable bytes defeat content-addressed
            # deduplication.
            content=json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode(),
            filename=f"prices-{start.isoformat()}-to-{end.isoformat()}.json",
            dataset="food-prices",
            source_url=pihps_prices.BASE,
            media_type="application/json",
            partition=(f"year={end.year}",),
            metadata={"from": start.isoformat(), "to": end.isoformat(), "rows": len(rows)},
        )
