"""Bank Indonesia's monthly surveys, and its food price portal.

Each lands the file the agency publishes, exactly as served — the survey ZIPs,
the price portal's JSON — and leaves the reading of it to extraction. The
vendored modules beside these know how to reach each source and how to parse
what comes back; only the first half is used here (program.md §2.1).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import date, timedelta

import requests

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
from ..ratelimit import HostRateLimiter
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

    Queried rather than downloaded, so a run lands the grid it asked for. The
    grid has four dimensions and the source walks three of them.

    *Price type* is the one that is easy to miss. The portal prices the same
    foods in four different markets — traditional, modern, wholesale and
    farmgate — and they are four series, not four readings of one. Each becomes
    its own indicator downstream; here it is simply another axis to walk.

    *Geography* is national plus each of the 34 provinces. The province list is
    fetched rather than hardcoded, and comes back in the names the geography
    registry already knows.

    *Time* is paged by calendar month. The endpoint's cost grows faster than
    the window it is given (see `pihps_prices`), so a year asked for in twelve
    requests returns in a fifth of the time it takes asked for in one.

    A full pull is a long run: 35 places by four price types by a month per
    window from 2017-03 is some sixteen thousand requests. It is meant to be
    run once, with `--since`, and then left to the daily window.
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
        notes=(
            "Vendored scraper. Daily strategic food prices, nationally and by "
            "province, across four market types. Coverage starts 2017-03."
        ),
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        end = date.today()
        start = ctx.since or end - timedelta(days=self.DEFAULT_DAYS)
        # Asking for 2016 is not an error, it is an empty table — so clamping
        # here is what keeps a `--since 2010` backfill from spending an hour
        # fetching nothing.
        start = max(start, pihps_prices.COVERAGE_START)

        # The declared ceiling, enforced. A scraper holds no reference to the
        # runner's limiter, so it keeps its own for the one host it touches —
        # and this source is the one that most needs it, being the only one
        # that makes five figures of requests in a sitting.
        limiter = HostRateLimiter(default_rate=self.meta.max_requests_per_second)

        # One session across the whole walk. A backfill makes thousands of
        # requests to one host, and a new connection for each is the difference
        # between a long run and a much longer one.
        with requests.Session() as session:
            places = self._places(ctx, session, limiter)

            for price_type_id, price_type in pihps_prices.PRICE_TYPES.items():
                for geo_id, geo_name in places:
                    for window_start, window_end in pihps_prices.month_windows(start, end):
                        limiter.acquire(pihps_prices.BASE)
                        rows = pihps_prices.fetch(
                            window_start.isoformat(),
                            window_end.isoformat(),
                            province_id=geo_id,
                            price_type_id=price_type_id,
                            session=session,
                        )
                        if not rows:
                            # A province with no market reporting that month.
                            # Landing an empty table would be landing the
                            # absence of data as though it were data.
                            continue

                        yield Artifact(
                            # Re-serialized compactly: the API does not promise
                            # stable whitespace, and unstable bytes defeat
                            # content-addressed deduplication.
                            content=json.dumps(
                                rows, separators=(",", ":"), ensure_ascii=False
                            ).encode(),
                            filename=(
                                f"prices-{price_type}-{_slug(geo_name)}-{window_start:%Y-%m}.json"
                            ),
                            dataset="food-prices",
                            source_url=pihps_prices.BASE,
                            media_type="application/json",
                            partition=(
                                f"price_type={price_type}",
                                f"geo={_slug(geo_name)}",
                                f"year={window_start.year}",
                            ),
                            # The province and the price type are request
                            # parameters, not columns: nothing inside the JSON
                            # says which market or which place it describes.
                            # Recorded here is the only place they survive, and
                            # the extractor reads them back off this record.
                            metadata={
                                "from": window_start.isoformat(),
                                "to": window_end.isoformat(),
                                "rows": len(rows),
                                "price_type": price_type,
                                "price_type_id": price_type_id,
                                "price_type_name": pihps_prices.PRICE_TYPE_NAMES[price_type_id],
                                "geo_name": geo_name,
                                "geo_level": "country" if geo_id == "" else "province",
                                "province_id": geo_id,
                            },
                        )

    def _places(
        self, ctx: ScrapeContext, session: requests.Session, limiter: HostRateLimiter
    ) -> list[tuple[str | int, str]]:
        """National, then every province — unless the run asks for less.

        `--param scope=national` keeps a smoke run to four requests a month
        instead of a hundred and forty.
        """
        national: list[tuple[str | int, str]] = [("", "Indonesia")]
        if ctx.params.get("scope") == "national":
            return national

        limiter.acquire(pihps_prices.ROOT)
        provinces = pihps_prices.provinces(session=session)
        # `Gorontalo` names both a province and a regency within it, and the
        # geography registry refuses a name that could be either. Qualifying
        # every province rather than special-casing one keeps the rows
        # uniform, and `Provinsi` is stripped as noise for the 33 names that
        # were never ambiguous.
        return national + [(p["id"], f"Provinsi {p['name']}") for p in provinces]


def _slug(name: str) -> str:
    """A place name as a RAW path segment."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
