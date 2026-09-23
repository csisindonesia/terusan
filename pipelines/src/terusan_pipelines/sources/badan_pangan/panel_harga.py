"""Panel Harga Pangan — daily food prices, from the other side of the market.

Bank Indonesia's PIHPS, already collected as `bi-pihps-food-prices`, surveys
prices in markets. This is the food agency's own panel: the same commodities,
collected through its own enumerators, published daily at producer, wholesale
and consumer level for every province. Two independent measures of the same
prices is not redundancy — it is the only way to notice when one of them is
wrong, and they diverge in exactly the weeks that matter.

The dashboard is public. The API behind it, `api-panelhargav2`, stopped being:
it now answers `Unauthorized. Invalid or missing API key.` to every call,
including the ones its own dashboard makes from a browser session. So this
needs a key from Bapanas, set as `PANEL_HARGA_API_KEY`, and is registered
inactive until one exists.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

from ..base import (
    Category,
    CollectionMethod,
    ScrapeContext,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..credentials import credentials
from ..http import Fetcher
from ..portals import AccessNotProvisioned, ApiSource, Endpoint

API = "https://api-panelhargav2.badanpangan.go.id/api/front"

#: The three levels the panel publishes, by the portal's own `level_harga_id`.
LEVELS = {1: "producer", 2: "wholesale", 3: "consumer"}

#: Days per run. The table endpoint takes a date range, and a month at a time
#: keeps a backfill to a dozen calls per level.
WINDOW_DAYS = 30


class PanelHarga(ApiSource):
    """Daily food prices at producer, wholesale and consumer level."""

    meta = SourceMeta(
        slug="badanpangan-panel-harga",
        name="Panel Harga Pangan — Badan Pangan Nasional",
        organization="Badan Pangan Nasional",
        category=Category.STATISTICS,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
        base_url="https://panelharga.badanpangan.go.id/",
        license="Public sector information",
        update_frequency=UpdateFrequency.DAILY,
        max_requests_per_second=1.0,
        active=False,
        # The schedule it should run on once a key exists: daily, after the
        # panel publishes the day's prices.
        schedule="0 7 * * *",
        notes=(
            "Needs PANEL_HARGA_API_KEY from Bapanas. The second independent "
            "measure of Indonesian food prices beside BI's PIHPS survey."
        ),
    )

    def request_headers(self) -> dict[str, str]:
        secret = credentials().panel_harga_api_key
        if secret is None:
            raise AccessNotProvisioned(
                "badanpangan-panel-harga needs PANEL_HARGA_API_KEY in .env; "
                "request a key from Badan Pangan Nasional"
            )
        return {**super().request_headers(), "x-api-key": secret.get_secret_value()}

    def endpoints_for(self, ctx: ScrapeContext, http: Fetcher) -> Iterator[Endpoint]:
        end = date.today()
        start = ctx.since or (end - timedelta(days=WINDOW_DAYS))

        for level_id, level in LEVELS.items():
            period = f"{start:%d/%m/%Y} - {end:%d/%m/%Y}"
            yield Endpoint(
                dataset=f"food-prices-{level}",
                url=(
                    f"{API}/harga-pangan-table?province_id=&level_harga_id={level_id}"
                    f"&period_date={period}"
                ),
                filename=f"panel-harga-{level}-{start:%Y%m%d}-{end:%Y%m%d}.json",
                partition=(f"year={end:%Y}",),
                metadata={
                    "level": level,
                    "level_harga_id": level_id,
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                },
            )
