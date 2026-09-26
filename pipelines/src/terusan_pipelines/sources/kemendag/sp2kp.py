"""SP2KP — the Ministry of Trade's food price monitoring system.

Published through a Tableau view that only yields its crosstab to a real
browser, so this source needs Playwright. It reports that plainly rather than
failing obscurely: a source that cannot run is a gap in the run summary, not a
crash (program.md §39).
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
from .legacy import sp2kp_prices


class FoodPriceMonitoring(Source):
    meta = SourceMeta(
        slug="kemendag-sp2kp-prices",
        name="SP2KP — Sistem Pemantauan Pasar dan Kebutuhan Pokok",
        organization="Kementerian Perdagangan",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url=sp2kp_prices.TOKEN_URL,
        license="Kementerian Perdagangan open data",
        update_frequency=UpdateFrequency.DAILY,
        max_requests_per_second=0.5,
        schedule="0 8 * * *",
        notes=(
            "Vendored scraper. Needs the `browser` extra — `uv sync --extra "
            "browser && uv run playwright install chromium` — because the "
            "Tableau view hands over its crosstab only to a real browser. "
            "`available()` reports that, and the schedule tests refuse to let "
            "a source that cannot run stay scheduled.\n\n"
            "Not redundant with BI's PIHPS despite both being daily food "
            "prices: PIHPS surveys 34 provinces and this reports 513 "
            "kabupaten/kota, and it carries Minyakita, wheat flour and the "
            "government ceiling beside each price, none of which PIHPS has.\n\n"
            "One day per run, and no way to ask for another: the view exports "
            "the day it is showing. History accumulates forward from the first "
            "run and cannot be backfilled."
        ),
    )

    @staticmethod
    def available() -> bool:
        return sp2kp_prices.sync_playwright is not None

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        if not self.available():
            raise RuntimeError(
                "playwright is not installed; add the `browser` extra "
                "(uv sync --extra browser && uv run playwright install chromium)"
            )

        today = date.today()
        yield Artifact(
            content=sp2kp_prices.download_crosstab_csv(),
            filename=f"prices-{today.isoformat()}.csv",
            # Not `food-prices`, which is PIHPS's and is declared in the
            # dataset registry as Bank Indonesia's. Landing Kemendag's
            # per-city figures there would file one ministry's survey under
            # another institution's name.
            dataset="sp2kp-food-prices",
            source_url=sp2kp_prices.TOKEN_URL,
            media_type="text/csv",
            partition=(f"year={today.year}",),
        )
