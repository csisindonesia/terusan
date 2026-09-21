"""Trading Economics — every Indonesian indicator it publishes.

The Indonesian-language portal carries one page per indicator —
`/indonesia/foreign-exchange-reserves`, `/indonesia/inflation-rate` — and an
index listing them all. This walks the index and lands each detail page.

Indonesia only. Trading Economics covers 196 countries through the same URL
shape, and widening `COUNTRY` would land two hundred times the pages for a
warehouse whose subject is one country. Comparators are a deliberate addition,
not the default (the same argument as the World Bank source's `COUNTRIES`).

A detail page carries the latest reading, the previous one, the all-time high
and low with their dates, the release calendar and the unit — but no history
beyond that: the chart behind it is a paid API. So history accrues by running
daily and keeping each day's page. Landing is content-addressed, which makes
that cheap: a day where nothing was published writes nothing, and a day where
one figure changed writes one directory.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date

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
from ..ratelimit import HostRateLimiter

log = structlog.get_logger(__name__)

ROOT = "https://id.tradingeconomics.com"
COUNTRY = "indonesia"
INDEX_URL = f"{ROOT}/{COUNTRY}/indicators"

#: The dataset the index itself lands under. Named rather than folded into the
#: indicators so a reader can see which indicators existed on a given day —
#: the list is not fixed, and an indicator that disappears leaves no other
#: trace.
#:
#: This name reaches the portal: Silver observations carry the Bronze dataset
#: they came from, and the catalogue lists collections by it. So it reads as a
#: collection a person would recognise rather than as a crawler's scratch file.
INDEX_DATASET = "indonesia-indicators"

#: Indicator links, as the page writes them: single-quoted, lowercase, hyphenated.
_LINK = re.compile(rf"href=['\"]/{COUNTRY}/([a-z0-9-]+)['\"]")

#: Pages under /indonesia/ that are not indicators. The index links to all
#: three, and fetching them would land a calendar as though it were a series.
NOT_INDICATORS = frozenset({"indicators", "calendar", "forecast", "news"})

#: A guard against an index that changes shape. Trading Economics publishes
#: about a hundred Indonesian indicators; a regex that suddenly matches
#: thousands means the page changed and the run should stop rather than crawl
#: the site.
MAX_INDICATORS = 400


def indicator_url(slug: str) -> str:
    return f"{ROOT}/{COUNTRY}/{slug}"


def indicator_slugs(html: str) -> list[str]:
    """The indicators an index page links to, in a stable order.

    Sorted rather than left in page order: the order the site lists them in is
    presentational and shifts, and a stable order makes two runs comparable.
    """
    found = {slug for slug in _LINK.findall(html) if slug not in NOT_INDICATORS}
    return sorted(found)


class TradingEconomicsIndonesia(Source):
    """Indonesia's indicator pages, one artifact each, daily."""

    meta = SourceMeta(
        slug="tradingeconomics-indonesia",
        name="Trading Economics — Indikator Ekonomi Indonesia",
        organization="Trading Economics",
        category=Category.STATISTICS,
        # A commercial aggregator: it carries Bank Indonesia's and BPS's
        # figures without publishing on their behalf, which is neither a
        # government portal nor a government API.
        source_type=SourceType.MARKET_DATA,
        collection_method=CollectionMethod.SCRAPE,
        base_url=INDEX_URL,
        country="ID",
        license=(
            "Trading Economics terms of use — internal research only; "
            "redistribution of the figures is not granted"
        ),
        update_frequency=UpdateFrequency.DAILY,
        # 05:00 local. Bank Indonesia and BPS release in the morning, and a
        # run before the working day means the warehouse holds the previous
        # day's revisions before anyone reads them.
        schedule="0 5 * * *",
        # One page every two seconds: about a hundred indicators, so a run
        # takes three or four minutes and never looks like a crawl. robots.txt
        # disallows only the ASP.NET handlers, not these pages.
        max_requests_per_second=0.5,
        notes=(
            "Indonesia only. Walks /indonesia/indicators and lands each "
            "indicator page. The site publishes no history without a paid "
            "API, so history accrues one daily snapshot at a time."
        ),
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        today = date.today()
        # Monthly partitions: a hundred pages a day is thirty-six thousand
        # directories a year, and a year in one directory is slow to list on
        # a NAS long before it is slow to read.
        partition = (f"year={today.year}", f"month={today.month:02d}")

        # The declared ceiling, enforced. `max_requests_per_second` is only
        # advisory until a source hands a limiter to its client: the runner
        # keeps one per host, but a scraper has no reference to the runner, so
        # this one covers the only source on id.tradingeconomics.com. A hundred
        # pages at half a request a second is three and a half minutes, which
        # is the difference between a daily reader and a crawl.
        limiter = HostRateLimiter(default_rate=self.meta.max_requests_per_second)

        with fetcher(limiter=limiter) as http:
            index = http.get(INDEX_URL)
            index_html = index.text

            slugs = indicator_slugs(index_html)
            if not slugs:
                raise ValueError(
                    f"no indicator links found at {INDEX_URL}; the index page changed shape"
                )
            if len(slugs) > MAX_INDICATORS:
                raise ValueError(
                    f"{INDEX_URL} linked {len(slugs)} indicators, more than the "
                    f"{MAX_INDICATORS} a country page should have; refusing to crawl"
                )

            log.info("tradingeconomics.index", indicators=len(slugs))

            yield Artifact(
                content=index.content,
                filename=f"indicators-{today.isoformat()}.html",
                dataset=INDEX_DATASET,
                source_url=str(index.url),
                media_type="text/html",
                partition=partition,
                metadata={"indicator_count": len(slugs), "indicators": slugs},
            )

            landed = 1
            for slug in slugs:
                if ctx.limit is not None and landed >= ctx.limit:
                    return

                url = indicator_url(slug)
                # One indicator missing must not cost the other hundred: an
                # indicator is retired or renamed every few months, and the
                # index keeps linking to it for a while afterwards.
                response = http.try_get(url)
                if response is None:
                    continue

                yield Artifact(
                    content=response.content,
                    filename=f"{slug}-{today.isoformat()}.html",
                    dataset=slug,
                    source_url=str(response.url),
                    media_type="text/html",
                    partition=partition,
                    metadata={"indicator": slug, "observed_on": today.isoformat()},
                )
                landed += 1
